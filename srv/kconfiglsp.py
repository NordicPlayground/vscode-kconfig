from typing import Union, Optional, List, Dict
import kconfiglib
import sys
import os
import re
import enum
import argparse
from west.app.main import WestApp
from lsp import CompletionItemKind, DocumentStore, Diagnostic, LSPServer, MarkupContent, Position, RPCError, Location, RPCNotification, Uri, TextDocument, Range, handler

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

class West(WestApp):

	def build(self, pristine=False, board=None, build_dir=None, source_dir=None, target=None, cmake_options=[]):
		args = []
		if pristine:
			args.append('-p')
		if board:
			args.append('-b')
			args.append(board)
		if build_dir:
			args.append('-d')
			args.append(build_dir)
		if target:
			args.append('-t')
			args.append(target)
		if source_dir:
			args.append(source_dir)

		if cmake_options:
			args.append('--')
			args.extend(cmake_options)

		return self.run(args)

	def modules(self):
		result = {}
		for line in self.run(['list', '-f', '{name}:{path}']).splitlines():
			name, path = line.split(':', 1)
			result[name] = path;
		return result

KCONFIG_WARN_LVL=Diagnostic.WARNING
ID_SEP = '@'

class KconfigErrorCode(enum.IntEnum):
	UNKNOWN_NODE = 1
	DESYNC = 2

class Kconfig(kconfiglib.Kconfig):
	def __init__(self, docs: DocumentStore, filename='Kconfig'):
		self.diags: Dict[str, Diagnostic] = {}
		self.docs = docs
		super().__init__(filename, True, False)
		self.warn_assign_undef = True
		self.warn_assign_override = True
		self.warn_assign_redun = True

	# Overriding _open to work on virtual file storage when required:
	def _open(self, filename, mode):
		doc = self.docs.get(Uri.file(filename))
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
	def __init__(self, name: str, range: Range, value: str, value_range: Range):
		self.name = name
		self.range = range
		self.value = value
		self.value_range = value_range

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
				entries.append(ConfEntry(match[2], range, match[3], value_range))
		return entries

	def find(self, name) -> List[ConfEntry]:
		return [entry for entry in self.entries() if entry.name == name]


class KconfigContext:
	"""A single instance of a kconfig compilation.
	   Represents one configuration of one application, equalling a single
	   build in Zephyr.
	"""

	def __init__(self, docs: DocumentStore, root, conf_files: List[ConfFile]=[], env={}, id=0):
		self.env = env
		self.conf_files = conf_files
		self.id = id
		self.version = 0
		self.docs = docs
		self._root = root
		self._kconfig: Optional[kconfiglib.Kconfig] = None
		self.menu = None
		self.cmd_diags = []
		self.west = West()
		# for file in conf_files:
		# 	file.doc.on_change(lambda _: self.load_config())

	@property
	def build_dir(self):
		return f'build_kconfig_{self.id}'

	def parse(self):
		self.menu = None
		self.modified = {}
		self.clear_diags()

		functions_path = os.path.join(self.env['ZEPHYR_BASE'], 'scripts', 'kconfig')
		if not functions_path in sys.path:
			sys.path.append(functions_path)

		self._kconfig = Kconfig(self.docs, self._root)
		self.version += 1

	def has_file(self, uri):
		return any([(file.doc.uri == uri) for file in self.conf_files])

	def _node_id(self, node: kconfiglib.MenuNode):
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
		if filter.startswith('CONFIG_'):
			filter = filter[len('CONFIG_'):]
		return [sym for sym in self._kconfig.syms.values() if _filter_match(filter, sym.name)]

	def symbol_search(self, query):
		return map(_symbolitem, self.symbols(query))

	def _check_user_vals(self):
		for sym in self._kconfig.syms.values():
			if sym.user_value is None:
				continue

			user_val = sym.user_value
			if sym.type in (kconfiglib.BOOL, kconfiglib.TRISTATE):
				user_val = kconfiglib.TRI_TO_STR[user_val]

			if user_val == sym.str_value:
				continue

			if len(sym.str_value):
				warn = f'CONFIG_{sym.name} was assigned the value {user_val}, but got the value {sym.str_value}.'
			else:
				warn = f'CONFIG_{sym.name} couldn\'t be set.'
			deps = [kconfiglib.expr_str(dep) for dep in _missing_deps(sym)]
			if deps:
				warn += ' Missing dependencies:\n'
				warn += ' && '.join(deps)

			for file in self.conf_files:
				entries = file.find(sym.name)
				for entry in entries:
					file.diags.append(Diagnostic(warn, entry.range))

		for file in self.conf_files:
			for entry in file.entries():
				if entry.name in self._kconfig.syms:
					actual: kconfiglib.Symbol = self._kconfig.syms[entry.name]
					if actual.type == kconfiglib.BOOL:
						if entry.value not in ['y', 'n']:
							file.diags.append(Diagnostic.err(f'Expected "y" or "n"', entry.value_range))
					elif actual.type == kconfiglib.HEX:
						if not re.match(r'^0x[a-fA-F\d]+$', entry.value):
							file.diags.append(Diagnostic.err(f'Expected hex value', entry.value_range))
					elif actual.type == kconfiglib.INT:
						if not re.match(r'^\d+$', entry.value):
							file.diags.append(Diagnostic.err(f'Expected integer', entry.value_range))


	def _lint(self):
		self._check_user_vals()

	def load_config(self):
		self.clear_diags()

		for i, file in enumerate(self.conf_files):
			self._kconfig.load_config(file.doc.uri.path, replace=(i == 0))
		self._lint()

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
		doc = self.docs.get(uri)
		if not doc:
			return

		word = doc.word_at(pos)
		if word and word.startswith('CONFIG_'):
			return self.get(word[len('CONFIG_'):])


class BoardConf:
	def __init__(self, name, arch, dir):
		self.name = name
		self.arch = arch
		self.dir = dir

	@property
	def conf_files(self):
		return [os.path.join(self.dir, self.name + '_defconfig')]


class KconfigServer(LSPServer):
	def __init__(self, istream=None, ostream=None):
		super().__init__('zephyr-kconfig', VERSION, istream, ostream)
		self._next_id = 0
		self.last_ctx = None
		self.board_conf = BoardConf('nrf52dk_nrf52832', 'arm', '/home/trond/ncs/zephyr/boards/arm/nrf52dk_nrf52832')
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
		ctx = KconfigContext(self.docs, root, [ConfFile(self.docs.create(Uri.file(file))) for file in self.board_conf.conf_files] + conf_files, env, id)
		self.dbg('Parsing...')
		ctx.parse()
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

	@handler('kconfig/createCtx')
	def handle_create_ctx(self, params):
		ctx = self.create_ctx(params['root'], [ConfFile(f) for f in params['conf']], params['env'])
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

	@handler('textDocument/didOpen')
	def handle_open(self, params):
		result = super().handle_open(params)
		if params['textDocument'].get('languageId') == 'properties':
			self.create_ctx('Kconfig', [ConfFile(self.docs.get(Uri.parse(params['textDocument']['uri'])))], {})
		return result

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

		doc = self.docs.get(uri)
		if not doc:
			self.dbg('Unknown document')
			return

		pos = Position.create(params['position'])
		line = doc.line(pos.line)
		prefix = line[:pos.character]
		word = prefix.lstrip()
		if len(word) > 0 and not word.startswith('CONFIG_'):
			self.dbg('Word "{}" doesnt start with CONFIG_ on line "{}" (prefix: "{}")'.format(word, line, prefix))
			return

		items = [{
				'label': 'CONFIG_' + sym.name,
				'kind': CompletionItemKind.VARIABLE,
				'detail': kconfiglib.TYPE_TO_STR[sym.type],
				'documentation': next((n.help.replace('\n', ' ') for n in sym.nodes if n.help), ' ')
				# TODO: Add snippet completion and completion resolve
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

def wait_for_debugger():
	import debugpy
	# 5678 is the default attach port in the VS Code debug configurations.
	debugpy.listen(5678)
	debugpy.wait_for_client()

def parse_args():
	parser = argparse.ArgumentParser()
	parser.add_argument('--debug', action='store_true', help='Enable debug mode. Will wait for a debugger to attach before starting the server.')
	parser.add_argument('--west', type=str, help='Path to West')
	return parser.parse_args()

if __name__ == "__main__":
	args = parse_args()

	if args.debug:
		wait_for_debugger()

	srv = KconfigServer()
	srv.loop()
