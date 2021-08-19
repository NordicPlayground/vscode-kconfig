import inspect
import os
from typing import Union, Optional, List, Dict
import sys
import re
import json
import enum
from datetime import datetime

JSONRPC = '2.0'

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
	def __init__(self, istream=None, ostream=None):
		self._send_stream = ostream if ostream else sys.stdout
		self._recv_stream = istream if istream else sys.stdin
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
		sys.stderr.write('\n'.join(*args) + '\n')
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

	@property
	def basename(self):
		return os.path.basename(self.path)

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
		self.lines = []
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

	@staticmethod
	def err(message, range):
		return Diagnostic(message, range, Diagnostic.ERROR)

	@staticmethod
	def warn(message, range):
		return Diagnostic(message, range, Diagnostic.WARNING)

	@staticmethod
	def info(message, range):
		return Diagnostic(message, range, Diagnostic.INFORMATION)

	@staticmethod
	def hint(message, range):
		return Diagnostic(message, range, Diagnostic.HINT)

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

documentStore = DocumentStore()

class LSPServer(RPCServer):
	def __init__(self, name: str, version: str, istream, ostream):
		super().__init__(istream, ostream)
		self.rootUri: str
		self.workspaceFolders: List[WorkspaceFolder]
		self.name = name
		self.version = version
		self.trace = 'off'

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
