from ..lsp import Position, Range, TextDocument, Uri
import os.path as path

# Test the LSP TextDocument class and accompanying classes

start_range = Range(Position(0, 0), Position(0, 0))
test_file = path.join(path.dirname(__file__), 'resources', 'test.txt')

def test_position():
    with open(test_file, 'r') as f:
        doc = TextDocument(Uri.file(test_file), f.read())
    assert doc.pos(0) == Position(0, 0)
    assert doc.pos(138) == Position(1, 28)


def test_replace():
    doc = TextDocument(Uri.file('/some/file.txt'))
    doc.replace('the first line', start_range)
    assert doc.text == 'the first line\n'

    doc.replace('<insert>', start_range)
    assert doc.text == '<insert>the first line\n'

    doc.replace('<replace>', Range(Position(0, 0), Position(0, len('<insert>'))))
    assert doc.text == '<replace>the first line\n'

    doc.replace('<replace>', Range(Position(0, 13), Position(0, 18)))
    assert doc.text == '<replace>the <replace> line\n'

    doc.replace('', Range(Position(0, 13), Position(0, 23)))
    assert doc.text == '<replace>the line\n'

    doc.replace('\nsecond ', Range(Position(0, 12), Position(0, 13)))
    assert doc.text == '<replace>the\nsecond line\n'

    doc.replace('updated line', Range(Position(1, 0), Position(1, 9999)))
    assert doc.text == '<replace>the\nupdated line\n'

    # delete second line:
    doc.replace('', Range(Position(1, 0), Position(1, 9999)))
    assert doc.text == '<replace>the\n'

    # add more lines:
    doc.replace('\n\n\n', Range(Position(0, 9999), Position(0, 9999)))
    assert doc.text == '<replace>the\n\n\n\n'

    # Replace multiple lines:
    doc.replace('abc\ndef', Range(Position(1, 0), Position(3, 9999)))
    assert doc.text == '<replace>the\nabc\ndef\n'


def test_lines():
    pass
