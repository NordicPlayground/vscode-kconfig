import functools
import inspect
from typing import Union, Optional, Callable, List, Dict
import kconfiglib
import sys
import os
import re
import json
import enum
from datetime import datetime

JSONRPC = '2.0'
VERSION = '1.0'

def encode_json(o):
	def encoder(obj):
		if hasattr(obj, 'to_dict'):
			return obj.to_dict()
		return obj.__dict__
	return json.dumps(o, default=encoder)

class RPCMsg:
	def __init__(self, jsonrpc: str):
		self.jsonrpc = jsonrpc

class RPCRequest(RPCMsg):
	def __init__(self, id: Union[str, int], method: str, params: Union[object, list]=None):
		super().__init__(JSONRPC)
		self.id = id
		self.method = method
		self.params = params


class RPCErrorCode(enum.IntEnum):
	PARSE_ERROR = -32700
	INVALID_REQUEST = -32600
	METHOD_NOT_FOUND = -32601
	INVALID_PARAMS = -32602
	INTERNAL_ERROR = -32603
	SERVER_NOT_INITIALIZED = -32002
	UNKNOWN_ERROR_CODE = -32001
	CONTENT_MODIFIED = -32801
	REQUEST_CANCELLED = -32800


class RPCError(Exception):
	def __init__(self, code: int, message: str, data=None):
		super().__init__()
		self.code = code
		self.message = message
		self.data = data

	def to_dict(self):
		return {"code": self.code, "message": self.message, "data": self.data}

class RPCResponse(RPCMsg):
	def __init__(self, id: Optional[Union[str, int]]=None, result=None, error: RPCError=None):
		super().__init__(JSONRPC)
		self.id = id
		self.result = result
		self.error = error

class RPCNotification(RPCMsg):
	def __init__(self, method: str, params=None):
		super().__init__(JSONRPC)
		self.method = method
		self.params = params

def handler(method):
	def wrapper(f):
		f._rsp_method = method
		return f
	return wrapper

class RPCServer:
	def __init__(self):
		self._send_stream = sys.stdout
		self._recv_stream = sys.stdin
		self.req = None
		self.log_file = 'lsp.log'
		self.running = True
		self.handlers = {}
		for method_name, _ in inspect.getmembers(self.__class__):
			method = getattr(self.__class__, method_name)
			if hasattr(method, '_rsp_method'):
				self.handlers[method._rsp_method] = method

		# Flush log file:
		with open(self.log_file, 'a') as f:
			f.write('=' * 80 + '\n')

	def dbg(self, *args):
		with open(self.log_file, 'a') as f:
			for line in args:
				f.write('dbg: ' + str(line) + '\n')

	def log(self, *args):
		with open(self.log_file, 'a') as f:
			for line in args:
				f.write('inf: ' + str(line) + '\n')

	def _read_headers(self):
		length = 0
		content_type = ''
		while True:
			line = self._recv_stream.readline().strip()
			if len(line) == 0:
				return length, content_type

			parts = [p.strip() for p in line.split(':')]
			if len(parts) != 2:
				continue

			[key, value] = parts

			if key == 'Content-Length':
				length = int(value)
			elif key == 'Content-Type':
				content_type = value

	def rsp(self, result=None, error: RPCError =None):
		if not self.req:
			raise Exception('No command')

		self.send(RPCResponse(self.req.id, result, error))
		self.req = None


	def send(self, msg: RPCMsg):
		raw = encode_json(msg)
		self.dbg('send: ' + raw)
		self._send_stream.write(
			'Content-Type: "application/vscode-jsonrpc; charset=utf-8"\r\nContent-Length: {}\r\n\r\n{}'.format(len(raw), raw))
		self._send_stream.flush()

	def _recv(self) -> Union[RPCNotification, RPCRequest]:
		length, _ = self._read_headers()
		self.dbg('Receiving {} bytes...'.format(length))
		data = self._recv_stream.read(length)
		self.dbg('data: {}'.format(data))
		obj = json.loads(data)

		if 'id' in obj:
			self.req = RPCRequest(obj['id'], obj['method'], obj['params'])
			return self.req

		return RPCNotification(obj['method'], obj['params'])

	def handle(self, msg: Union[RPCNotification, RPCRequest]):
		self.dbg('{} Method: {}'.format(type(msg).__name__, msg.method))

		if msg.method in self.handlers:
			error = None
			result = None
			start = datetime.now()
			try:
				result = self.handlers[msg.method](self, msg.params)
			except RPCError as e:
				self.dbg('Failed with error ' + str(e))
				error = e
				raise e
			except Exception as e:
				self.dbg('Failed with error ' + str(e))
				error = RPCError(RPCErrorCode.UNKNOWN_ERROR_CODE, 'Exception: "{}"'.format(e.args))
				raise e

			end = datetime.now()
			self.dbg('Handled in {} us'.format((end - start).microseconds))

			if self.req:
				self.rsp(result, error)
		else:
			self.dbg('No handler for "{}"'.format(msg.method))
			if self.req:
				self.rsp(None, RPCError(RPCErrorCode.METHOD_NOT_FOUND, 'Unknown method "{}"'.format(msg.method)))

	def loop(self):
		try:
			while self.running:
				self.handle(self._recv())
		except KeyboardInterrupt:
			pass


#################################################################################################################################
# Language Server Protocol Server
#################################################################################################################################


class Uri:
	def __init__(self, scheme:str, authority:str='', path: str='', query:str=None, fragment:str=None):
		self.scheme = scheme
		self.authority = authority
		self.path = path
		self.query = query
		self.fragment = fragment


	def __repr__(self):
		uri = '{}://{}{}'.format(self.scheme, self.authority, self.path)
		if self.query:
			uri += '?' + self.query
		if self.fragment:
			uri += '#' + self.fragment
		return uri

	def __str__(self):
		return self.__repr__()

	def __eq__(self, o: object) -> bool:
		if isinstance(o, str):
			return Uri.parse(o) == self
		if not isinstance(o, Uri):
			return NotImplemented
		return str(self) == str(o)

	@staticmethod
	def parse(raw: str):
		def sanitize(part):
			if part:
				return re.sub(r'%(\d+)', lambda x: chr(int(x.group(1))), part)
			else:
				return ''

		if not isinstance(raw, str):
			return NotImplemented

		match = re.match(r'(.*?)://(.*?)(/[^?\s]*)(?:\?([^#]+))?(?:#(.+))?', raw)
		if match:
			return Uri(*[sanitize(p) for p in match.groups()])

	@staticmethod
	def file(path: str):
		return Uri('file', '', path)

	def to_dict(self):
		return str(self)


class WorkspaceFolder:
	def __init__(self, uri: Uri, name: str):
		self.uri = uri
		self.name = name


class Position:
	def __init__(self, line: int, character: int):
		self.line = line
		self.character = character

	@property
	def range(self):
		return Range(self, self)

	def before(self, other):
		if not isinstance(other, Position):
			return NotImplemented
		return (self.line < other.line) or (self.line == other.line and self.character < other.character)

	def __eq__(self, other):
		if not isinstance(other, Position):
            		return False
		return self.line == other.line and self.character == other.character

	def __repr__(self):
		return '{}:{}'.format(self.line + 1, self.character)

	@staticmethod
	def create(obj):
		return Position(obj['line'], obj['character'])

class Range:
	def __init__(self, start: Position, end: Position):
		self.start = start
		self.end = end

	def single_line(self):
		return self.start.line == self.end.line

	@staticmethod
	def union(a, b):
		if not isinstance(a, Range) or not isinstance(b, Range):
			return NotImplemented
		return Range(
			a.start if a.start.before(b.start) else b.start,
			b.end if a.end.before(b.end) else b.end
		)

	def contains(self, pos: Position):
		return (not pos.before(self.start)) and (not self.end.before(pos))

	def __eq__(self, other):
		if not isinstance(other, Range):
            		return NotImplemented

		return self.start == other.start and self.end == other.end

	def __repr__(self):
		return '{} - {}'.format(self.start, self.end)

	@staticmethod
	def create(obj):
		return Range(Position.create(obj['start']), Position.create(obj['end']))


class Location:
	def __init__(self, uri: Uri, range: Range):
		self.uri = uri
		self.range = range

	def __repr__(self):
		return '{}: {}'.format(self.uri, self.range)

	@staticmethod
	def create(obj):
		return Location(Uri.parse(obj['uri']), Range.create(obj['range']))


class TextDocument:
	UNKNOWN_VERSION=-1
	def __init__(self, uri: Uri, text: str = None, languageId: str = None, version: int = None):
		if version == None:
			version = TextDocument.UNKNOWN_VERSION

		self.uri = uri
		self.languageId = languageId
		self.version = version
		self.modified = version != 0
		self._inside = False
		self._mode = None
		self._scanpos = 0
		self._cbs = []
		self._virtual = self.uri.scheme != 'file'
		self.loaded = False
		if text:
			self._set_text(text)

	def _sanitize(self, text: str):
		return text.replace('\r', '')

	def on_change(self, cb):
		self._cbs.append(cb)

	def _set_text(self, text):
		self.lines = text.splitlines()
		self.loaded = True
		for cb in self._cbs:
			cb(self)

	@property
	def text(self):
		return '\n'.join(self.lines)

	def line(self, index):
		if index < len(self.lines):
			return self.lines[index]

	def offset(self, pos: Position):
		if pos.line == 0:
			return pos.character
		return len(''.join([l + '\n' for l in self.lines[:pos.line]])) + pos.character

	def pos(self, offset: int):
		content = self.text[:offset]
		lines = content.splitlines()
		return Position(len(lines) - 1, len(lines[-1]))

	def get(self, range: Range = None):
		if not range:
			return self.text
		return self.text[self.offset(range.start):self.offset(range.end)]

	def word_at(self, pos: Position):
		line = self.line(pos.line)
		return re.match(r'.*?(\w*)$', line[:pos.character])[1] + re.match(r'^\w*', line[pos.character:])[0]

	def replace(self, text:str, range: Range = None):
		if range:
			self._set_text(self.text[:self.offset(range.start)] + text + self.text[self.offset(range.end):])
		else:
			self._set_text(text)
		self.modified = True

	def _write_to_disk(self):
		if not self._virtual:
			with open(self.uri.path, 'w') as f:
				f.write(self.text)
			self.modified = False
			self.version = TextDocument.UNKNOWN_VERSION

	def _read_from_disk(self):
		# will raise environment error if the file doesn't exist. This has to be caught outside:
		with open(self.uri.path, 'r') as f:
			text = f.read()
		if text == None:
			raise IOError('Unable to read from file {}'.format(self.uri.path))

		self._set_text(text)
		self.modified = False
		self.version = TextDocument.UNKNOWN_VERSION

	# Standard File behavior:

	def __enter__(self):
		self._inside = True
		return self

	def __exit__(self, type, value, traceback):
		if self._inside:
			self._inside = False
			self.close()

	class LineIterator:
		def __init__(self, doc):
			self._linenr = 0
			self._lines = doc.lines

		def __next__(self):
			if self._linenr >= len(self._lines):
				raise StopIteration
			line = self._lines[self._linenr]
			self._linenr += 1
			return line

	def __iter__(self):
		return TextDocument.LineIterator(self)

	def open(self, mode='r'):
		if not mode in ['w', 'a', 'r']:
			raise IOError('Unknown mode ' + str(mode))

		if mode == 'w':
			self._set_text('')
			self.modified = True
			self.version = TextDocument.UNKNOWN_VERSION
		elif not self.loaded:
			self._read_from_disk()
		self._mode = mode
		self._scanpos = 0

	def close(self):
		if self._mode in ['a', 'w'] and self.modified:
			self._write_to_disk()
		self._mode = None

	def write(self, text: str):
		if not self._mode in ['a', 'w']:
			raise IOError('Invalid mode for writing: ' + str(self._mode))
		if not self.loaded:
			raise IOError('File not loaded in RAM: {}'.format(self.uri.path))

		self._set_text(self.text + text)
		if self._mode == 'a':
			self._scanpos = len(self.text)
		self.modified = True
		self.version = TextDocument.UNKNOWN_VERSION

	def writelines(self, lines):
		for line in lines:
			self.write(line)

	def read(self, length=None):
		if self._mode != 'r':
			raise IOError('Invalid mode for reading: ' + str(self._mode))

		if self._scanpos >= len(self.text):
			return ''

		if length == None:
			out = self.text[self._scanpos:]
			self._scanpos = len(self.text)
		else:
			out = self.text[self._scanpos:self._scanpos + length]
			self._scanpos += length
		return out

	def readline(self, size=None):
		if self._mode != 'r':
			raise IOError('Invalid mode for reading: ' + str(self._mode))

		if self._scanpos >= len(self.text):
			return ''
		out = self.text[self._scanpos:].splitlines(True)[0]
		if size != None:
			out = out[:size]
		self._scanpos += len(out)
		return out

	def readlines(self, _=None):
		if self._mode != 'r':
			raise IOError('Invalid mode for reading: ' + str(self._mode))

		if self._scanpos >= len(self.text):
			return []
		out = self.text[self._scanpos:].splitlines()
		self._scanpos = len(self.text)
		return out

	def flush(self):
		pass

	def seek(self, offset):
		if self._mode == None:
			raise IOError('Cannot seek on closed file')
		self._scanpos = offset

	def tell(self):
		return self._scanpos

	def next(self):
		if self._mode != 'r':
			raise IOError('Invalid mode for reading: ' + str(self._mode))
		if self._scanpos >= len(self.text):
			raise StopIteration
		return self.readline()


class DocProvider:
	def __init__(self, scheme: str):
		self.scheme = scheme

	def get(self, uri) -> Optional[TextDocument]:
		return None

	def exists(self, uri):
		return self.get(uri) != None

class DocumentStore:
	def __init__(self):
		self.docs: Dict[str, TextDocument] = {}
		self._providers: Dict[str, DocProvider] = {}

	def open(self, doc: TextDocument):
		self.docs[str(doc.uri)] = doc

	def close(self, uri: Uri):
		pass

	def provider(self, provider):
		self._providers[provider.uri.scheme] = provider

	def get(self, uri: Uri):
		if uri.scheme in self._providers:
			return self._providers[uri.scheme].get(uri)
		return self.docs.get(str(uri))

	def _from_disk(self, uri: Uri):
		with open(uri.path, 'r') as f: # will raise environment error if the file doesn't exist. This has to be caught outside
			text = f.read()
		if text == None:
			return None
		doc = TextDocument(uri, text)
		self.docs[str(uri)] = doc
		return doc

	def create(self, uri: Uri):
		doc = self.get(uri)
		if doc:
			return doc
		return self._from_disk(uri)


class CompletionItemKind(enum.IntEnum):
	TEXT = 1
	METHOD = 2
	FUNCTION = 3
	CONSTRUCTOR = 4
	FIELD = 5
	VARIABLE = 6
	CLASS = 7
	INTERFACE = 8
	MODULE = 9
	PROPERTY = 10
	UNIT = 11
	VALUE = 12
	ENUM = 13
	KEYWORD = 14
	SNIPPET = 15
	COLOR = 16
	FILE = 17
	REFERENCE = 18
	FOLDER = 19
	ENUM_MEMBER = 20
	CONSTANT = 21
	STRUCT = 22
	EVENT = 23
	OPERATOR = 24
	TYPE_PARAMETER = 25

class DiagnosticRelatedInfo:
	def __init__(self, loc, message):
		self.loc = loc
		self.message = message

class Diagnostic:
	ERROR = 1
	WARNING = 2
	INFORMATION = 3
	HINT = 4

	class Tag(enum.IntEnum):
		UNNECESSARY = 1
		DEPRECATED = 2

	def __init__(self, message, range, severity=WARNING):
		self.message = message
		self.range = range
		self.severity = severity
		self.tags = []
		self.related_info = []

	@staticmethod
	def severity_str(severity):
		return [
			'Unknown',
			'Error',
			'Information',
			'Hint'
		][severity]

	def __str__(self) -> str:
		return '{}: {}: {}'.format(self.range, Diagnostic.severity_str(self.severity), self.message)

	def to_dict(self):
		obj = {"message": self.message, "range": self.range, "severity": self.severity}
		if len(self.tags):
			obj['tags'] = self.tags
		if len(self.related_info):
			obj['relatedInformation'] = [info.__dict__ for info in self.related_info]

		return obj

class MarkupContent:
	PLAINTEXT = 'plaintext'
	MARKDOWN = 'markdown'
	def __init__(self, value='', kind=None):
		self.value = value
		self.kind = kind if kind else MarkupContent.MARKDOWN

	def _sanitize(self, text):
		return re.sub(r'[`<>{}\[\]]', r'\\\0', text)

	def add_text(self, text):
		if self.kind == MarkupContent.MARKDOWN:
			self.value += self._sanitize(text)
		else:
			self.value += text

	def add_markdown(self, md):
		if self.kind == MarkupContent.PLAINTEXT:
			self.value = self._sanitize(self.value)
			self.kind = MarkupContent.MARKDOWN
		self.value += md

	def paragraph(self):
		self.value += '\n\n'

	def linebreak(self):
		if self.kind == MarkupContent.MARKDOWN:
			self.value += '\n\n'
		else:
			self.value += '\n'

	def add_code(self, lang, code):
		self.add_markdown('\n```{}\n{}\n```\n'.format(lang, code))

	def add_link(self, url, text=''):
		self.add_markdown('[{}]({})'.format(text, url))


	@staticmethod
	def plaintext(value):
		return MarkupContent(value, MarkupContent.PLAINTEXT)

	@staticmethod
	def markdown(value):
		return MarkupContent(value, MarkupContent.MARKDOWN)

	@staticmethod
	def code(lang, value):
		return MarkupContent.markdown('```{}\n{}\n```'.format(lang, value))


class LSPServer(RPCServer):
	def __init__(self, name: str, version: str):
		super().__init__()
		self.rootUri: str
		self.workspaceFolders: List[WorkspaceFolder]
		self.name = name
		self.version = version
		self.trace = 'off'
		self.docs = DocumentStore()

	def capabilities(self):
		def has(method):
			return method in self.handlers

		caps = {
			'hoverProvider': has('textDocument/hover'),
			'declarationProvider': has('textDocument/declaration'),
			'definitionProvider': has('textDocument/definition'),
			'typeDefinitionProvider': has('textDocument/typeDefinition'),
			'implementationProvider': has('textDocument/implementation'),
			'referencesProvider': has('textDocument/references'),
			'documentHighlightProvider': has('textDocument/documentHighlight'),
			'documentSymbolProvider': has('textDocument/documentSymbol'),
			'codeActionProvider': has('textDocument/codeAction'),
			'colorProvider': has('textDocument/documentColor'),
			'documentFormattingProvider': has('textDocument/formatting'),
			'documentRangeFormattingProvider': has('textDocument/rangeFormatting'),
			'renameProvider': has('textDocument/rename'),
			'foldingRangeProvider': has('textDocument/foldingRange'),
			'selectionRangeProvider': has('textDocument/selectionRange'),
			'linkedEditingRangeProvider': has('textDocument/linkedEditingRange'),
			'callHierarchyProvider': has('textDocument/prepareCallHierarchy'),
			'monikerProvider': has('textDocument/moniker'),
			'workspaceSymbolProvider': has('workspace/symbol'),
			'textDocumentSync': 2, # incremental
			# 'signatureHelpProvider'
			# 'codeLensProvider'
			# 'documentLinkProvider'
			# 'documentOnTypeFormattingProvider'
			# 'executeCommandProvider'
			# 'semanticTokensProvider'
			# workspace?: {
			# 	workspaceFolders?: WorkspaceFoldersServerCapabilities;
			# 	fileOperations?: {
			# 		didCreate?: FileOperationRegistrationOptions;
			# 		willCreate?: FileOperationRegistrationOptions;
			# 		didRename?: FileOperationRegistrationOptions;
			# 		willRename?: FileOperationRegistrationOptions;
			# 		didDelete?: FileOperationRegistrationOptions;
			# 		willDelete?: FileOperationRegistrationOptions;
			# 	}
			# }
			# experimental?: any;
		}

		if has('textDocument/completion'):
			caps['completionProvider'] = {}

		return caps

	def dbg(self, *args):
		super().dbg(*args)
		if self.trace != 'off':
			self.send(RPCNotification('$/logTrace', {'message': '\n'.join(args)}))

	def log(self, *args):
		super().log(*args)
		if self.trace == 'message':
			self.send(RPCNotification('$/logTrace', {'message': '\n'.join(args)}))

	@handler('$/setTrace')
	def handle_set_trace(self, params):
		self.trace = params['value']

	@handler('$/cancelRequest')
	def handle_cancel(self, params):
		pass

	@handler('$/progress')
	def handle_progress(self, params):
		pass

	@handler('shutdown')
	def handle_shutdown(self, params):
		self.running = False

	@handler('initialize')
	def handle_initialize(self, params):
		self.rootUri = params['rootUri']
		if 'trace' in params:
			self.trace = params['trace']
		if 'workspaceFolders' in params:
			self.dbg('workspaceFolders: ' + str(params['workspaceFolders']))
			self.workspaceFolders = [WorkspaceFolder(Uri.parse(folder['uri']), folder['name']) for folder in params['workspaceFolders']]
		return {
			'capabilities': self.capabilities(),
			'serverInfo': {
				'name': self.name,
				'version': self.version
			}
		}

	@handler('textDocument/didOpen')
	def handle_open(self, params):
		doc = params['textDocument']
		uri = Uri.parse(doc['uri'])
		self.docs.open(TextDocument(uri, doc['text'], doc['languageId'], doc['version']))

	@handler('textDocument/didChange')
	def handle_change(self, params):
		uri = Uri.parse(params['textDocument']['uri'])
		doc = self.docs.get(uri)
		if not doc:
			return

		for change in params['contentChanges']:
			if 'range' in change:
				range = Range.create(change['range'])
			else:
				range = None

			doc.replace(change['text'], range)

		doc.version = params['textDocument']['version']

	@handler('textDocument/didClose')
	def handle_close(self, params):
		self.docs.close(Uri.parse(params['textDocument']['uri']))


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


class ConfFile:
	def __init__(self, doc: TextDocument):
		self.doc = doc
		self.diags = []

	def find(self, name):
		entries = []
		for linenr, line in enumerate(self.doc.lines):
			match = re.match(r'^\s*(CONFIG_' + name + r')\s*\=', line)
			if match:
				entries.append(Range(Position(linenr, match.start(1)), Position(linenr, match.end(1))))
		return entries


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
		# for file in conf_files:
		# 	file.doc.on_change(lambda _: self.load_config())

	def parse(self):
		self.menu = None
		self.modified = {}
		self.clear_diags()
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
				warn = f'CONFIG_{sym.name} couldn\'t be set.'
			else:
				warn = f'CONFIG_{sym.name} was assigned the value {user_val}, but got the value {sym.str_value}.'
			deps = [kconfiglib.expr_str(dep) for dep in _missing_deps(sym)]
			if deps:
				warn += ' Missing dependencies:\n'
				warn += ' && '.join(deps)

			for file in self.conf_files:
				entries = file.find(sym.name)
				for range in entries:
					file.diags.append(Diagnostic(warn, range))

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
	def __init__(self):
		super().__init__('zephyr-kconfig', VERSION)
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
		# if sym.user_value == None:
		contents.add_markdown("Value: `{}`".format(sym.str_value))
		# else:
		# 	contents.add_markdown("Value: `{}`".format(sym.user_value))
		contents.paragraph()

		help = '\n\n'.join([n.help.replace('\n', ' ') for n in sym.nodes if n.help])
		if help:
			contents.add_text(help)

		return {'contents': contents}

def launch_debug_server():
	import debugpy
	# 5678 is the default attach port in the VS Code debug configurations.
	debugpy.listen(5678)
	debugpy.wait_for_client()


if __name__ == "__main__":
	launch_debug_server()
	srv = KconfigServer()
	srv.loop()
