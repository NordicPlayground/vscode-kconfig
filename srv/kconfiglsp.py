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

	def toJSON(self):
		return '{"code": {}, "message": {}, "data": {}}'.format(self.code, self.message, self.data)

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
		with open(self.log_file, 'w') as f:
			pass

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

		self.dbg('Responding: ' + str(result))
		if error:
			self.dbg('Error: ' + str(error))

		self.send(RPCResponse(self.req.id, result, error))
		self.req = None


	def send(self, msg: RPCMsg):
		raw = json.dumps(msg, default=lambda o: o.__dict__)
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
			except Exception as e:
				self.dbg('Failed with error ' + str(e))
				error = RPCError(RPCErrorCode.UNKNOWN_ERROR_CODE, 'Exception: "{}"'.format(e.args))
				raise

			end = datetime.now()
			self.dbg('Handled in {} us'.format((end - start).microseconds))

			if self.req:
				self.rsp(result, error)
		else:
			self.dbg('No handler for ' + str(msg.method))
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
		uri = '{}://{}/{}'.format(self.scheme, self.authority, self.path)
		if self.query:
			uri += '?' + self.query
		if self.fragment:
			uri += '#' + self.fragment
		return uri

	@staticmethod
	def parse(raw: str):
		if not isinstance(raw, str):
			return NotImplemented
		parts = re.match(r'(.*?)://(.*?)/([^?]+)(?:\?([^#]+))?(?:#(.+))?', raw)
		if parts:
			return Uri(parts[1], parts[2], parts[3], parts[4], parts[5])

	@staticmethod
	def file(path: str):
		return Uri('file', '', path)


class WorkspaceFolder:
	def __init__(self, uri: Uri, name: str):
		self.uri = uri
		self.name = name


class Position:
	def __init__(self, line: int, character: int):
		self.line = line
		self.character = character

	def to_range(self):
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
	def __init__(self, uri: Uri, text: str = None, languageId: str = None, version: int = 0):
		self.uri = uri
		self.languageId = languageId
		self.version = version
		self.modified = version != 0
		self._inside = False
		self._mode = None
		self._scanpos = 0
		self._virtual = self.uri.scheme != 'file'
		if text:
			self._text = self._sanitize(text)


	def _sanitize(self, text: str):
		return text.replace('\r', '')

	@property
	def lines(self):
		return self._text.splitlines()

	def offset(self, pos: Position):
		if pos.line == 0:
			return pos.character
		return len(''.join([l + '\n' for l in self.lines[:pos.line]])) + pos.character

	def pos(self, offset: int):
		content = self._text[:offset]
		lines = content.splitlines()
		return Position(len(lines) - 1, len(lines[-1]))

	def get(self, range: Range = None):
		if not range:
			return self._text
		return self._text[self.offset(range.start):self.offset(range.end)]

	def word_at(self, pos: Position):
		line = self.lines[pos.line]
		return re.match(r'.*?(\w*)$', line[:pos.character])[1] + re.match(r'^\w*', line[pos.character:])[0]

	def replace(self, text:str, range: Range = None):
		text = self._sanitize(text)
		if range:
			self._text = self._text[:self.offset(range.start)] + text + self._text[self.offset(range.end):]
		else:
			self._text = text
		self.modified = True

	def _write_to_disk(self):
		if not self._virtual:
			with open(self.uri.path, 'w') as f:
				f.write(self._text)
			self.modified = False
			self.version = -1
	# Standard File behavior:

	def __enter__(self):
		self._inside = True

	def __exit__(self, type, value, traceback):
		if self._inside:
			self._inside = False
			self.close()

	def open(self, mode='r'):
		if not mode in ['w', 'a', 'r']:
			raise IOError('Unknown mode ' + str(mode))
		if mode == 'w':
			self._text = ''
			self.modified = True
			self.version = -1
		self._mode = mode
		self._scanpos = 0

	def close(self):
		if self._mode in ['a', 'w']:
			self._write_to_disk()
		self._mode = None

	def write(self, text: str):
		if not self._mode in ['a', 'w']:
			raise IOError('Invalid mode for writing: ' + str(self._mode))
		self._text += self._sanitize(text)
		if self._mode == 'a':
			self._scanpos = len(self._text)
		self.modified = True
		self.version = -1

	def writelines(self, lines):
		for line in lines:
			self.write(line)

	def read(self, length=None):
		if self._mode != 'r':
			raise IOError('Invalid mode for reading: ' + str(self._mode))

		if self._scanpos >= len(self._text):
			return ''

		if length == None:
			out = self._text[self._scanpos:]
			self._scanpos = len(self._text)
		else:
			out = self._text[self._scanpos:self._scanpos + length]
			self._scanpos += length
		return out

	def readline(self, size=None):
		if self._mode != 'r':
			raise IOError('Invalid mode for reading: ' + str(self._mode))

		if self._scanpos >= len(self._text):
			return ''
		out = self._text[self._scanpos:].splitlines(True)[0]
		if size != None:
			out = out[:size]
		self._scanpos += len(out)
		return out

	def readlines(self, _=None):
		if self._mode != 'r':
			raise IOError('Invalid mode for reading: ' + str(self._mode))

		if self._scanpos >= len(self._text):
			return []
		out = self._text[self._scanpos:].splitlines()
		self._scanpos = len(self._text)
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
		if self._scanpos >= len(self._text):
			return StopIteration
		return self.readline()

	def __iter__(self):
		return self.lines.__iter__()



class DocumentStore:
	def __init__(self):
		self.docs: Dict[str, TextDocument] = {}

	def open(self, doc: TextDocument):
		self.docs[str(doc.uri)] = doc

	def close(self, uri: Uri):
		pass

	def get(self, uri: Uri):
		handle = str(uri)
		if handle in self.docs:
			return self.docs[handle]
		return self._from_disk(uri)

	def _from_disk(self, uri: Uri):
		with open(uri.path, 'r') as f: # will raise environment error if the file doesn't exist. This has to be caught outside
			text = f.read()
		if text == None:
			return None
		doc = TextDocument(uri, text)
		self.docs[str(uri)] = doc
		return doc

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

		return {
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
			'textDocumentSync': 2 # incremental
			# 'completionProvider'
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


class Kconfig(kconfiglib.Kconfig):
	def __init__(self, docs: DocumentStore, filename='Kconfig'):
		super().__init__(filename, True, False)
		self.docs = docs

	# Overriding _open to work on virtual file storage:
	def _open(self, filename, mode):
		doc = self.docs.get(Uri.file(filename))
		doc.open(mode)
		return doc

def _prompt(sym: kconfiglib.Symbol):
	for node in sym.nodes:
		if node.prompt:
			return node.prompt

class KconfigServer(LSPServer):
	def __init__(self):
		super().__init__('zephyr-kconfig', VERSION)

	@handler('initialize')
	def handle_initialize(self, params):
		options = params['initializationOptions'] #TODO
		if 'env' in options:
			for key, val in enumerate(options['env']):
				os.environ[key] = val

		self.kconfig = Kconfig(self.docs, options['kconfigFile'])
		return super().handle_initialize(params)

	@handler('kconfig/menuItems')
	def handle_menuitems(self, params):
		ctx = params['ctx']
		menu = params['menu']
		# self.kconfig.top_node


	@handler('kconfig/setVal')
	def handle_setval(self, params):
		pass

	@handler('kconfig/getEntry')
	def handle_getentry(self, params):
		pass

	def completion_match(self, filter: str, name: str):
		return name.startswith(filter)

	@handler('textDocument/completion')
	def handle_completion(self, params):
		doc = self.docs.get(Uri.parse(params['textDocument']['uri']))
		if not doc:
			return
		pos = Position.create(params['position'])
		prefix = doc.lines[pos.line][:pos.character]
		match = re.match(r'.*(\w*)$', prefix)
		if not match:
			return

		word = match[1]
		if not word.startswith('CONFIG_'):
			return
		word = word[len('CONFIG_'):]
		syms = [self.kconfig.syms[key] for key in self.kconfig.syms.keys() if self.completion_match(word, key)]
		result = []
		for sym in syms:
			if not _prompt(sym):
				continue

			result.append({
				'label': 'CONFIG_' + sym.name,
				'kind': CompletionItemKind.VARIABLE,
				'detail': kconfiglib.TYPE_TO_STR[sym.type],
				'documentation': '\n\n'.join([_prompt(sym)] + [n.help for n in sym.nodes if n.help])
			})
		return result


if __name__ == "__main__":
	srv = KconfigServer()
	srv.loop()
