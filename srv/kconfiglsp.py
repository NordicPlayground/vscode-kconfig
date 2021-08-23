from typing import Union, Optional, List, Dict
import kconfiglib
import sys
import os
import re
import enum
import argparse
from lsp import CodeAction, CodeActionKind, CompletionItemKind, Diagnostic, InsertTextFormat, LSPServer, MarkupContent, Position, RPCError, Location, RPCNotification, Snippet, TextEdit, Uri, TextDocument, Range, WorkspaceEdit, handler, documentStore

VERSION = '1.0'

#################################################################################################################################
# Kconfig LSP Server
#################################################################################################################################


# Environment variables passed to menuconfig:
# - ZEPHYR_BASE
# - ZEPHYR_TOOLCHAIN_VARIANT -> default to "zephyr"
# - PYTHON_EXECUTABLE
# - srctree=${ZEPHYR_BASE}
# - KERNELVERSION from ./VERSION, as a hex number, see version.cmake
# - KCONFIG_CONFIG=${PROJECT_BINARY_DIR}/.config
# - ARCH
# - ARCH_DIR
# - BOARD_DIR
# - SHIELD_AS_LIST
# - KCONFIG_BINARY_DIR=${CMAKE_BINARY_DIR}/Kconfig
# - TOOLCHAIN_KCONFIG_DIR -> default to ${TOOLCHAIN_ROOT}/cmake/toolchain/${ZEPHYR_TOOLCHAIN_VARIANT}
# - EDT_PICKLE
# - ZEPHYR_{modules}_MODULE_DIR -> get from west?
# - EXTRA_DTC_FLAGS -> Appear to be unused
# - DTS_POST_CPP -> ${PROJECT_BINARY_DIR}/${BOARD}.dts.pre.tmp
# - DTS_ROOT_BINDINGS -> ${DTS_ROOTs}/dts/bindings


KCONFIG_WARN_LVL=Diagnostic.WARNING
ID_SEP = '@'

class KconfigErrorCode(enum.IntEnum):
	UNKNOWN_NODE = 1
	DESYNC = 2
	PARSING_FAILED = 3

class Kconfig(kconfiglib.Kconfig):
	def __init__(self, filename='Kconfig'):
		self.diags: Dict[str, List[Diagnostic]] = {}
		super().__init__(filename, True, False)
		self.warn_assign_undef = True
		self.warn_assign_override = True
		self.warn_assign_redun = True

	# Overriding _open to work on virtual file storage when required:
	def _open(self, filename, mode):
		doc = documentStore.get(Uri.file(filename))
		if doc:
			doc.open(mode)
			return doc
		if os.path.isdir(filename):
			raise kconfiglib.KconfigError(f'Attempting to open directory {filename} as file @{self.filename}:{self.linenr}')
		return super()._open(filename, mode)

	def _warn(self, msg, filename=None, linenr=None):
		super()._warn(msg, filename, linenr)
		if not filename:
			filename = ''
		if not linenr:
			linenr = 1

		if not filename in self.diags:
			self.diags[filename] = []

		self.diags[filename].append(Diagnostic(msg, Position(int(linenr-1), 0).range, KCONFIG_WARN_LVL))


def _prompt(sym: kconfiglib.Symbol):
	for node in sym.nodes:
		if node.prompt and kconfiglib.expr_value(node.prompt[1]):
			return node.prompt[0]

def _visible(node):
    return node.prompt and kconfiglib.expr_value(node.prompt[1]) and not \
        (node.item == kconfiglib.MENU and not kconfiglib.expr_value(node.visibility))

def _children(node):
	children = []
	node = node.list
	while node:
		children.append(node)
		if node.list and not node.is_menuconfig:
			children.extend(_children(node))
		node = node.next

	return children

def _suboption_depth(node):
	"""In menuconfig, ndoes that aren't children of menuconfigs are rendered
	   in the same menu, but indented. Get the depth of this indentation.
	"""
	parent = node.parent
	depth = 0
	while not parent.is_menuconfig:
		depth += 1
		parent = parent.parent
	return depth

def _val(sym: kconfiglib.Symbol):
	if sym.orig_type == kconfiglib.STRING:
		return sym.str_value
	if sym.orig_type in (kconfiglib.INT, kconfiglib.HEX):
		return int(sym.str_value)
	if sym.orig_type == kconfiglib.BOOL:
		return sym.tri_value != 0
	if sym.orig_type == kconfiglib.TRISTATE:
		return sym.tri_value

def _path(node):
	"""Unique path ID of each node, allowing us to identify each node in a menu"""
	if node.parent:
		i = 0
		it = node.parent.list
		while it and it != node:
			it = it.next
			i += 1
		if not it:
			raise RPCError(KconfigErrorCode.DESYNC, 'Tree is invalid')
		return _path(node.parent) + [i]
	return [0]

def _loc(sym: kconfiglib.Symbol):
	return [Location(Uri.file(os.path.join(n.kconfig.srctree, n.filename)), Position(n.linenr-1, 0).range) for n in sym.nodes]

def _next(node, count=1):
	while count > 0 and node:
		node = node.next
		count -= 1
	return node

def _symbolitem(sym: kconfiglib.Symbol):
	item = {
		'name': sym.name,
		'visible': sym.visibility > 0,
		'type': kconfiglib.TYPE_TO_STR[sym.type],
		'help': next((n.help for n in sym.nodes if n.help), '')
	}

	prompt = _prompt(sym)
	if prompt:
		item['prompt'] = prompt
	return item

def _filter_match(filter: str, name: str):
	return name.startswith(filter) # TODO: implement fuzzy match?


def _missing_deps(sym):
	deps = kconfiglib.split_expr(sym.direct_dep, kconfiglib.AND)

	if sym.type in (kconfiglib.BOOL, kconfiglib.TRISTATE):
		return [dep for dep in deps if kconfiglib.expr_value(dep) < sym.user_value]
	# string/int/hex
	return [dep for dep in deps if kconfiglib.expr_value(dep) == 0]


class KconfigMenu:
	def __init__(self, ctx, node: kconfiglib.MenuNode, id):
		self.ctx = ctx
		self.node = node
		self.id = id

	@property
	def name(self):
		return str(self.node)

	def _menuitem(self, node):
		sym = node.item
		item = {
			'visible': _visible(node),
			'loc': Location(Uri.file(node.filename), Position(node.line, 0).range),
			'is_menu': node.is_menuconfig,
			'depth': _suboption_depth(node),
			'id': self.ctx._node_id(node),
		}

		if node.prompt:
			item['prompt'] = node.prompt[0]

		if 'help' in node:
			item['help'] = node['help']

		if isinstance(sym, kconfiglib.Symbol):
			item['type'] = kconfiglib.TYPE_TO_STR[sym.orig_type]
			item['val'] = _val(sym)
			item['name'] = sym.name
			if 'assignable' in sym:
				item['options'] = list(sym.assignable)

		return item

	@property
	def items(self):
		return [self._menuitem(node) for node in _children(self.node)]

	def to_dict(self):
		return {
			'name': self.name,
			'id': self.id,
			'items': self.items,
		}

class ConfEntry:
	def __init__(self, name: str, loc: Location, assignment: str, value_range: Range):
		self.name = name
		self.loc = loc
		self.raw = assignment.strip()
		self.value_range = value_range

	@property
	def range(self):
		return self.loc.range

	@property
	def full_range(self):
		return Range(self.range.start, self.value_range.end)

	def is_string(self):
		return self.raw.startswith('"') and self.raw.endswith('"')

	def is_bool(self):
		return self.raw in ['y', 'n']

	def is_hex(self):
		return re.match(r'0x[a-fA-F\d]+', self.raw)

	def is_int(self):
		return re.match(r'\d+', self.raw)

	@property
	def value(self):
		if self.is_string():
			return self.raw[1:-1] # strip out quotes
		if self.is_bool():
			return self.raw
		if self.is_hex():
			return int(self.raw, 16)
		if self.is_int():
			return int(self.raw)

	@property
	def type(self):
		if self.is_string():
			return kconfiglib.TYPE_TO_STR[kconfiglib.STRING]
		if self.is_hex():
			return kconfiglib.TYPE_TO_STR[kconfiglib.HEX]
		if self.is_int():
			return kconfiglib.TYPE_TO_STR[kconfiglib.INT]
		if self.is_bool():
			return kconfiglib.TYPE_TO_STR[kconfiglib.BOOL]

		return kconfiglib.TYPE_TO_STR[kconfiglib.UNKNOWN]

	@property
	def line_range(self):
		"""Entire line range."""
		return Range(
			Position(self.range.start.line, 0), Position(self.range.start.line + 1, 0))

	def remove(self, title='Remove entry') -> CodeAction:
		"""Create a code action that will remove this entry"""
		action = CodeAction(title)
		action.edit.add(self.loc.uri, TextEdit.remove(self.line_range))
		return action


class ConfFile:
	def __init__(self, uri: Uri):
		self.uri = uri
		self.diags: List[Diagnostic] = []

	@property
	def doc(self) -> TextDocument:
		return documentStore.get(self.uri)

	def entries(self) -> List[ConfEntry]:
		entries = []
		for linenr, line in enumerate(self.doc.lines):
			match = re.match(r'^\s*(CONFIG_(\w+))\s*\=("[^"]+"|\w+)', line)
			if match:
				range = Range(
					Position(linenr, match.start(1)), Position(linenr, match.end(1)))
				value_range = Range(
					Position(linenr, match.start(3)), Position(linenr, match.end(3)))
				entries.append(ConfEntry(match[2], Location(self.uri, range), match[3], value_range))
		return entries

	def find(self, name) -> List[ConfEntry]:
		return [entry for entry in self.entries() if entry.name == name]


class BoardConf:
	def __init__(self, name, arch, dir):
		self.name = name
		self.arch = arch
		self.dir = dir

	@property
	def conf_file(self):
		return os.path.join(self.dir, self.name + '_defconfig')


class KconfigContext:
	"""A single instance of a kconfig compilation.
	   Represents one configuration of one application, equalling a single
	   build in Zephyr.
	"""

	def __init__(self, root, conf_files: List[ConfFile]=[], env={}, id=0):
		self.env = env
		self.conf_files = conf_files
		self.id = id
		self.board = BoardConf(env['BOARD'], env['ARCH'], env['BOARD_DIR'])
		self.version = 0
		self._root = root
		self._kconfig: Optional[Kconfig] = None
		self.menu = None
		self.cmd_diags: List[Diagnostic] = []
		# for file in conf_files:
		# 	file.doc.on_change(lambda _: self.load_config())

	@property
	def build_dir(self):
		return f'build_kconfig_{self.id}'

	def parse(self):
		self.menu = None
		self.modified = {}
		self.clear_diags()
		for key, value in self.env.items():
			os.environ[key] = value

		functions_path = os.path.join(self.env['ZEPHYR_BASE'], 'scripts', 'kconfig')
		if not functions_path in sys.path:
			sys.path.append(functions_path)

		self._kconfig = Kconfig(self._root)
		self.version += 1

	def has_file(self, uri):
		return any([(file.doc.uri == uri) for file in self.conf_files])

	def _node_id(self, node: kconfiglib.MenuNode):
		if not self._kconfig:
			return ''

		if node == self._kconfig.top_node:
			parts = ['MAINMENU']
		elif node.item == kconfiglib.MENU:
			parts = ['MENU', str(self._kconfig.menus.index(node))]
		elif isinstance(node.item, kconfiglib.Symbol):
			parts = ['SYM', node.item.name, str(node.item.nodes.index(node))]
		elif isinstance(node.item, kconfiglib.Choice):
			parts = ['CHOICE', self._kconfig.choices.index(node)]
		elif node.item == kconfiglib.COMMENT:
			parts = ['COMMENT', self._kconfig.comments.index(node)]
		else:
			parts = ['UNKNOWN', node.filename, node.linenr]

		parts.insert(0, str(self.version))

		return ID_SEP.join(parts)

	def find_node(self, id):
		[version, type, *parts] = id.split(ID_SEP)

		if int(version) != self.version:
			# Since we're building on the exact layout of the internals of the
			# kconfig tree, the node IDs depend on the fact that the tree is unchanged:
			return None

		if type == 'MENU':
			return self._kconfig.menus[int(parts[0])]

		if type == 'SYM':
			return self._kconfig.syms[parts[0]].nodes[int(parts[1])]

		if type == 'CHOICE':
			return self._kconfig.choices[int(parts[0])]

		if type == 'COMMENT':
			return self._kconfig.comments[int(parts[0])]

		if type == 'MAINMENU':
			return self._kconfig.top_node

	def get_menu(self, id=None):
		if not id:
			if not self.menu:
				return
			id = self.menu

		node = self.find_node(id)
		if not node:
			return
		return KconfigMenu(node, id)

	def set(self, name, val):
		sym = self.get(name)
		if not sym:
			raise RPCError(KconfigErrorCode.UNKNOWN_NODE, 'Unknown symbol {}'.format(name))
		valid = sym.set_value(val)
		if valid and not name in self.modified:
			self.modified.append(name)

	def unset(self, name):
		sym = self.get(name)
		if sym:
			sym.unset_value()

	def get(self, name) -> kconfiglib.Symbol:
		if self._kconfig:
			return self._kconfig.syms.get(name)

	def conf_file(self, uri):
		return next((file for file in self.conf_files if file.doc.uri == uri), None)

	def diags(self, uri):
		conf = self.conf_file(uri)
		if conf:
			return conf.diags

	def clear_diags(self):
		if self._kconfig:
			self._kconfig.diags.clear()
		self.cmd_diags.clear()
		for conf in self.conf_files:
			conf.diags.clear()

	def symbols(self, filter):
		if filter and filter.startswith('CONFIG_'):
			filter = filter[len('CONFIG_'):]
		return [sym for sym in self._kconfig.syms.values() if not filter or _filter_match(filter, sym.name)]

	def symbol_search(self, query):
		return map(_symbolitem, self.symbols(query))

	def check_type(self, file: ConfFile, entry: ConfEntry, sym: kconfiglib.Symbol):
		if kconfiglib.TYPE_TO_STR[sym.type] != entry.type:
			diag = Diagnostic.err(
				f'Invalid type. Expected {kconfiglib.TYPE_TO_STR[sym.type]}', entry.full_range)

			# Add action to convert between hex and int:
			if sym.type in [kconfiglib.HEX, kconfiglib.INT] and (entry.is_hex() or entry.is_int()):
				action = CodeAction(
					'Convert value to ' + str(kconfiglib.TYPE_TO_STR[sym.type]))
				if sym.type == kconfiglib.HEX:
					action.edit.add(entry.loc.uri, TextEdit(
						entry.value_range, hex(entry.value)))
				else:
					action.edit.add(entry.loc.uri, TextEdit(
						entry.value_range, str(entry.value)))
				diag.add_action(action)

			file.diags.append(diag)
			return True

	def check_assignment(self, file: ConfFile, entry: ConfEntry, sym: kconfiglib.Symbol):
		user_value = sym.user_value
		if sym.type in [kconfiglib.BOOL, kconfiglib.TRISTATE]:
			user_value = kconfiglib.TRI_TO_STR[user_value]

		if user_value != sym.str_value:
			actions = []

			if len(sym.str_value):
				warn = f'CONFIG_{sym.name} was assigned the value {entry.raw}, but got the value {sym.str_value}.'
			else:
				warn = f'CONFIG_{sym.name} couldn\'t be set.'

			deps = _missing_deps(sym)
			if deps:
				warn += ' Missing dependencies:\n'
				warn += ' && '.join([kconfiglib.expr_str(dep) for dep in deps])
				edits = []

				for dep in deps:
					if isinstance(dep, kconfiglib.Symbol) and dep.type == kconfiglib.BOOL:
						dep_entry = next((entry for entry in file.entries() if entry.name == dep.name), None)
						if dep_entry:
							edits.append({'dep': dep.name, 'edit': TextEdit(dep_entry.value_range, 'y')})
						else:
							edits.append({'dep': dep.name, 'edit': TextEdit(Range(entry.line_range.start, entry.line_range.start), f'CONFIG_{dep.name}=y\n')})

				if len(edits) == 1:
					action = CodeAction(f'Enable CONFIG_{edits[0]["dep"]} to resolve dependency')
					action.edit.add(file.uri, edits[0]['edit'])
					actions.append(action)
				elif len(edits) > 1:
					action = CodeAction(f'Enable {len(edits)} entries to resolve dependencies')

					# Dependencies are registered with a "nearest first" approach in kconfiglib.
					# As the nearest dependency is likely lowest in the menu hierarchy, we'll
					# reverse the list of edits, so the highest dependency is inserted first:
					edits.reverse()

					for edit in edits:
						action.edit.add(file.uri, edit['edit'])
					actions.append(action)

			actions.append(entry.remove())

			diag = Diagnostic.warn(warn, entry.range)
			for action in actions:
				diag.add_action(action)

			file.diags.append(diag)
			return True

	def check_visibility(self, file: ConfFile, entry: ConfEntry, sym: kconfiglib.Symbol):
		if sym.visibility == 0:
			diag = Diagnostic.warn(f'Symbol CONFIG_{entry.name} cannot be set (has no prompt)', entry.full_range)
			diag.add_action(entry.remove())
			file.diags.append(diag)
			return True

	def check_defaults(self, file: ConfFile, entry: ConfEntry, sym: kconfiglib.Symbol):
		if sym._str_default() == sym.user_value:
			diag = Diagnostic.hint(f'Value is {entry.raw} by default', entry.full_range)
			diag.tags = [Diagnostic.Tag.UNNECESSARY]
			diag.add_action(entry.remove('Remove redundant entry'))
			file.diags.append(diag)
			return True

	def lint(self):
		for file in self.conf_files:
			entries = file.entries()
			for entry in entries:
				if not entry.name in self._kconfig.syms:
					continue

				sym: kconfiglib.Symbol = self._kconfig.syms[entry.name]

				if self.check_type(file, entry, sym):
					continue
				if self.check_assignment(file, entry, sym):
					continue
				if self.check_visibility(file, entry, sym):
					continue
				if self.check_defaults(file, entry, sym):
					continue

	def load_config(self):
		self.clear_diags()

		self._kconfig.load_config(self.board.conf_file, replace=True)

		for file in self.conf_files:
			self._kconfig.load_config(file.doc.uri.path, replace=False)

		self.lint()

		for filename, diags in self._kconfig.diags.items():
			if filename == '':
				self.cmd_diags.extend(diags)
			else:
				uri = Uri.file(filename)
				conf = self.conf_file(uri)
				if conf:
					conf.diags.extend(diags)
				else:
					self.cmd_diags.extend(diags)

	def symbol_at(self, uri, pos):
		doc = documentStore.get(uri)
		if not doc:
			return

		word = doc.word_at(pos)
		if word and word.startswith('CONFIG_'):
			return self.get(word[len('CONFIG_'):])

class KconfigServer(LSPServer):
	def __init__(self, istream=None, ostream=None):
		super().__init__('zephyr-kconfig', VERSION, istream, ostream)
		self._next_id = 0
		self.last_ctx = None
		self.ctx: Dict[int, KconfigContext] = {}
		self.dbg('Python version: ' + sys.version)

	def publish_diags(self, uri, diags):
		self.send(RPCNotification('textDocument/publishDiagnostics', {
			'uri': uri,
			'diagnostics': diags,
		}))

	def create_ctx(self, root, conf_files, env):
		self.dbg('Creating context...')
		id = self._next_id
		ctx = KconfigContext(root, conf_files, env, id)
		self.dbg('Parsing...')
		try:
			ctx.parse()
		except kconfiglib.KconfigError as e:
			self.dbg('Parsing failed: ' + str(e))
			raise RPCError(KconfigErrorCode.PARSING_FAILED, str(e))

		self.dbg('Load config...')
		try:
			ctx.load_config()
		except Exception as e:
			self.dbg('FAILED: ' + str(e.__cause__))
			raise
		self.dbg('Done. {} diags, {} warnings'.format(sum([len(file.diags) for file in ctx.conf_files]), len(ctx._kconfig.warnings)))

		self.ctx[id] = ctx
		self._next_id += 1
		self.last_ctx = ctx
		for conf in ctx.conf_files:
			self.publish_diags(conf.doc.uri, conf.diags)
		return ctx

	def best_ctx(self, uri):
		if self.last_ctx and self.last_ctx.has_file(uri):
			return self.last_ctx

		ctx = next((ctx for ctx in self.ctx.items() if ctx.has_file(uri)), None)
		if ctx:
			self.last_ctx = ctx
		return ctx

	def get_sym(self, params):
		uri = Uri.parse(params['textDocument']['uri'])
		ctx = self.best_ctx(uri)
		if not ctx:
			self.dbg('No context for {}'.format(uri.path))
			return

		return ctx.symbol_at(uri, Position.create(params['position']))

	@handler('kconfig/addBuild')
	def handle_add_build(self, params):
		ctx = self.create_ctx(params['root'], [ConfFile(Uri.file(f)) for f in params['conf']], params['env'])
		return {'id': ctx.id}

	@handler('kconfig/search')
	def handle_search(self, params):
		ctx = self.ctx[params['ctx']]
		if not ctx:
			raise RPCError(KconfigErrorCode.UNKNOWN_CTX, 'Unknown context')

		return {
			'ctx': params['ctx'],
			'query': params['query'],
			'symbols': ctx.symbol_search(params['query']),
		}

	# TODO: This attempts to create a virtual configuration from the open prj.conf file.
	# This needs a bit more thought to work, as we'll need to emulate the build files
	# @handler('textDocument/didOpen')
	# def handle_open(self, params):
	# 	result = super().handle_open(params)
	# 	if params['textDocument'].get('languageId') == 'properties':
	# 		self.create_ctx('Kconfig', [ConfFile(self.docs.get(Uri.parse(params['textDocument']['uri'])))], {})
	# 	return result

	@handler('textDocument/didChange')
	def handle_change(self, params):
		super().handle_change(params)
		if self.last_ctx:
			self.last_ctx.load_config()
			self.dbg(self.last_ctx._kconfig.warnings)
			self.dbg('Updating diags...')
			for file in self.last_ctx.conf_files:
				self.publish_diags(file.doc.uri, file.diags)
			self.dbg(f'Command line: {len(self.last_ctx.cmd_diags)}')
			self.publish_diags(Uri.file('command-line'), self.last_ctx.cmd_diags)

		# TODO: Add handling of Kconfig changes:
		# - Reparse the active configuration
		# - Mark other configurations as dirty
		# - Rerun last_ctx.load_config()?

	@handler('kconfig/setMenu')
	def handle_set_menu(self, params):
		ctx = self.ctx[params['ctx']]
		ctx.menu = params['id']
		return ctx.get_menu(params['id'])

	@handler('kconfig/setVal')
	def handle_setval(self, params):
		ctx = self.ctx[params['ctx']]
		if 'val' in params:
			ctx.set(params['name'], params['val'])
		else:
			ctx.unset(params['name'])

	# @handler('kconfig/getEntry')
	# def handle_getentry(self, params):
	# 	pass # TODO: Should get the "help" page for the entry

	@handler('textDocument/completion')
	def handle_completion(self, params):
		uri = Uri.parse(params['textDocument']['uri'])
		ctx = self.best_ctx(uri)
		if not ctx:
			self.dbg('No context for {}'.format(uri.path))
			return

		doc = documentStore.get(uri)
		if not doc:
			self.dbg('Unknown document')
			return

		pos = Position.create(params['position'])
		line = doc.line(pos.line)
		if line:
			prefix = line[:pos.character]
			word = prefix.lstrip()
			if len(word) > 0 and not word.startswith('CONFIG_'):
				word = 'CONFIG_' + word
		else:
			word = None

		def insert_text(sym: kconfiglib.Symbol):
			insert = Snippet('CONFIG_')
			insert.add_text(sym.name)
			insert.add_text('=')
			if sym.type in [kconfiglib.BOOL, kconfiglib.TRISTATE]:
				choices = [kconfiglib.TRI_TO_STR[val] for val in list(sym.assignable)]
				choices.reverse() # sym.assignable shows 'n' first, but user normally wants 'y'
				insert.add_choice(choices)
			elif sym.type == kconfiglib.STRING:
				insert.add_text('"')
				insert.add_tabstop()
				insert.add_text('"')
			elif sym.type == kconfiglib.HEX:
				insert.add_text('0x')
			else:
				pass # freeform value

			return insert.text

		items = [{
				'label': 'CONFIG_' + sym.name,
				'kind': CompletionItemKind.VARIABLE,
				'detail': kconfiglib.TYPE_TO_STR[sym.type],
				'documentation': next((n.help.replace('\n', ' ') for n in sym.nodes if n.help), ' '),
				'insertText': insert_text(sym),
				'insertTextFormat': InsertTextFormat.SNIPPET
			}
			for sym in ctx.symbols(word) if any(node.prompt for node in sym.nodes)]

		self.dbg('Filter: "{}" Total symbols: {} Results: {}'.format(word, len(ctx._kconfig.syms.items()), len(items)))
		return items

	@handler('textDocument/definition')
	def handle_definition(self, params):
		sym = self.get_sym(params)
		if sym:
			return _loc(sym)

	@handler('textDocument/hover')
	def handle_hover(self, params):
		sym = self.get_sym(params)
		if not sym:
			return

		contents = MarkupContent('')

		prompt = next((node.prompt[0] for node in sym.nodes if node.prompt), None)
		if prompt:
			contents.add_text(prompt)
		else:
			contents.add_text(sym.name_and_loc)

		contents.paragraph()
		contents.add_markdown('Type: `{}`'.format(kconfiglib.TYPE_TO_STR[sym.type]))
		contents.linebreak()
		contents.add_markdown("Value: `{}`".format(sym.str_value))
		contents.paragraph()

		help = '\n\n'.join([n.help.replace('\n', ' ') for n in sym.nodes if n.help])
		if help:
			contents.add_text(help)

		return {'contents': contents}

	@handler('textDocument/codeAction')
	def handle_code_action(self, params):
		uri = Uri.parse(params['textDocument']['uri'])
		ctx = self.best_ctx(uri)
		if not ctx:
			self.dbg('No context for {}'.format(uri.path))
			return

		conf = ctx.conf_file(uri)
		if not conf:
			self.dbg('No conf file for {}'.format(uri.path))
			return

		range: Range = Range.create(params['range'])
		actions = []
		for diag in conf.diags:
			if range.overlaps(diag.range):
				actions.extend(diag.actions)

		return actions

def wait_for_debugger():
	import debugpy
	# 5678 is the default attach port in the VS Code debug configurations.
	debugpy.listen(5678)
	debugpy.wait_for_client()

def parse_args():
	parser = argparse.ArgumentParser()
	parser.add_argument('--debug', action='store_true', help='Enable debug mode. Will wait for a debugger to attach before starting the server.')
	return parser.parse_args()

if __name__ == "__main__":
	args = parse_args()

	if args.debug:
		wait_for_debugger()

	srv = KconfigServer()
	srv.loop()
