import pytest

import re
from typing import AbstractSet, Mapping, Tuple, Union

from shell_parser.ast import Word, File, StdinTarget, StdoutTarget, StderrTarget, DefaultFile
from shell_parser.ast import RedirectionInput, RedirectionOutput, RedirectionAppend, OperatorAnd, OperatorOr
from shell_parser.ast import DescriptorRead, DescriptorWrite, CommandDescriptorClosed, CommandFileDescriptor, CommandDescriptor
from shell_parser.ast import Command
from shell_parser.ast import BadFileDescriptorException
from shell_parser.formatter import Formatter
from shell_parser.parser import Parser, EmptyInputException
from shell_parser.parser import UnclosedQuoteParserFailure, EmptyStatementParserFailure, EmptyRedirectParserFailure
from shell_parser.parser import UnexpectedStatementFinishParserFailure, InvalidRedirectionParserFailure, AmbiguousRedirectParserFailure


def make_match(msg: str) -> str:
    return "^" + re.escape(msg) + "$"


def assert_single_cmd(cmd: Command):
    assert cmd.pipe_command is None
    assert cmd.next_command is None
    assert cmd.next_command_operator is None


def make_descriptor(
        target: Union[File, DefaultFile],
        operator: Union[RedirectionInput, RedirectionOutput, RedirectionAppend],
    ) -> CommandDescriptor:
    file_descriptor = CommandFileDescriptor(
        target=target,
        operator=operator,
    )
    if isinstance(operator, RedirectionInput):
        return CommandDescriptor(
            mode=DescriptorRead(),
            descriptor=file_descriptor,
        )
    else:
        return CommandDescriptor(
            mode=DescriptorWrite(),
            descriptor=file_descriptor,
        )


DEFAULT_DESCRIPTOR_STDIN = make_descriptor(DefaultFile(target=StdinTarget()), operator=RedirectionInput())
DEFAULT_DESCRIPTOR_STDOUT = make_descriptor(DefaultFile(target=StdoutTarget()), operator=RedirectionOutput())
DEFAULT_DESCRIPTOR_STDERR = make_descriptor(DefaultFile(target=StderrTarget()), operator=RedirectionOutput())


def assert_descriptors(
        cmd: Command,
        *,
        defaults: AbstractSet[int] = frozenset((0, 1, 2)),
        files: Mapping[int, CommandDescriptor] = None,
        closed: AbstractSet[int] = frozenset(),
    ):
    descriptors = cmd.descriptors.descriptors
    checked_fds = set()

    not_set_defaults = defaults - closed
    if files is not None:
        not_set_defaults -= files.keys()

    for default_fd in not_set_defaults:
        assert descriptors[default_fd].descriptor.is_default_file is True
        checked_fds.add(default_fd)

    if files is not None:
        for file_fd, descriptor in files.items():
            assert descriptors[file_fd] == descriptor
            checked_fds.add(file_fd)

    for closed_fd in closed:
        assert isinstance(descriptors[closed_fd], CommandDescriptorClosed)
        checked_fds.add(closed_fd)

    assert checked_fds == descriptors.keys()


@pytest.fixture(scope="module")
def parser() -> Parser:
    return Parser()


@pytest.fixture(scope="module")
def formatter() -> Formatter:
    return Formatter()


def test_empty_string(parser: Parser):
    with pytest.raises(EmptyInputException, match=make_match("Input statement was empty or contained only whitespace.")):
        parser.parse("")


@pytest.mark.parametrize("line", (
    " ",
    "  ",
    "   ",
    "\n",
    "\t",
    "\t\t",
    " \t ",
    "\t \t",
    "   \t\t\t",
))
def test_only_whitespace(parser: Parser, line: str):
    with pytest.raises(EmptyInputException, match=make_match("Input statement was empty or contained only whitespace.")):
        parser.parse(line)


@pytest.mark.parametrize("line,expected_str", (
    ("plainword", Word("plainword")),
    ("'one word'", Word("one word")),
    ('"one word"', Word("one word")),
    ("' one word '", Word(" one word ")),
    ('" one word "', Word(" one word ")),
    (" plainword ", Word("plainword")),
    (" 'one word' ", Word("one word")),
    (' " one word " ', Word(" one word ")),
    (r"plain\word", Word("plainword")),
    (r"plain\ word", Word("plain word")),
    (r"'one\word'", Word(r"one\word")),
    (r'"one\word"', Word(r"one\word")),
    (r"'one\ word'", Word(r"one\ word")),
    (r'"one\ word"', Word(r"one\ word")),
))
def test_single_word(parser: Parser, line: str, expected_str: Word):
    first_cmd = parser.parse(line)
    assert first_cmd.command == expected_str
    assert len(first_cmd.args) == 0
    assert_single_cmd(first_cmd)
    assert first_cmd.asynchronous is False


@pytest.mark.parametrize("space_char", (" ", "\t"))
@pytest.mark.parametrize("before_space_count", range(0, 4))
@pytest.mark.parametrize("after_space_count", range(0, 4))
@pytest.mark.parametrize("manual,nonmanual", (
    ("plainword;", "plainword"),
    ("plainword ;", "plainword "),
    ("'one word';", "'one word'"),
    ("'one word' ;", "'one word'"),
    ('"one word";', '"one word"'),
    ('"one word" ;', '"one word"'),
    (r"plain\word;", r"plain\word"),
    (r"plain\ word;", r"plain\ word"),
    (r"'one\word';", r"'one\word'"),
    (r'"one\word";', r'"one\word"'),
    (r"'one\ word';", r"'one\ word'"),
    (r'"one\ word";', r'"one\ word"'),
))
def test_single_word_manually_terminated(parser: Parser, manual: str, nonmanual: str, before_space_count: int, after_space_count: int, space_char: str):
    before_spaces = space_char * before_space_count
    after_spaces = space_char * after_space_count
    _manual = manual.replace(";", before_spaces + ";" + after_spaces)
    manual_first_cmd = parser.parse(_manual)
    nonmanual_first_cmd = parser.parse(nonmanual)
    assert manual_first_cmd == nonmanual_first_cmd
    assert_single_cmd(manual_first_cmd)
    assert manual_first_cmd.asynchronous is False


@pytest.mark.parametrize("line,command_word,args_words", (
    ("cmd arg1", Word("cmd"), (Word("arg1"),)),
    ("cmd arg1 arg2", Word("cmd"), (Word("arg1"), Word("arg2"))),
    ("cmd arg1 arg2 arg3", Word("cmd"), (Word("arg1"), Word("arg2"), Word("arg3"))),
    ("'cmd' 'arg1 arg2' arg3", Word("cmd"), (Word("arg1 arg2"), Word("arg3"))),
    ('"cmd" "arg1" "arg2 arg3"', Word("cmd"), (Word("arg1"), Word("arg2 arg3"))),
    ("cmd  arg1   arg2", Word("cmd"), (Word("arg1"), Word("arg2"))),
    ("'cmd'   arg1  '   arg2'", Word("cmd"), (Word("arg1"), Word("   arg2"))),
    ("cmd 1arg 2arg", Word("cmd"), (Word("1arg"), Word("2arg"))),
    ("cmd\targ1", Word("cmd"), (Word("arg1"),)),
    ("cmd\targ1\targ2", Word("cmd"), (Word("arg1"), Word("arg2"))),
    ("cmd\targ1\targ2\targ3", Word("cmd"), (Word("arg1"), Word("arg2"), Word("arg3"))),
    ("'cmd'\t'arg1\targ2'\targ3", Word("cmd"), (Word("arg1\targ2"), Word("arg3"))),
    ('"cmd"\t"arg1"\t"arg2\targ3"', Word("cmd"), (Word("arg1"), Word("arg2\targ3"))),
    ("cmd \t arg1  \t arg2", Word("cmd"), (Word("arg1"), Word("arg2"))),
    ("'cmd' \t arg1\t ' \t  arg2'", Word("cmd"), (Word("arg1"), Word(" \t  arg2"))),
    ("cmd\t1arg\t2arg", Word("cmd"), (Word("1arg"), Word("2arg"))),
    ("cmd -- --", Word("cmd"), (Word("--"), Word("--"))),
    ("cmd \\-- -\\- \\-\\- --", Word("cmd"), (Word("--"), Word("--"), Word("--"), Word("--"))),
))
def test_multiple_words(parser: Parser, line: str, command_word: Word, args_words: Tuple[Word, ...]):
    first_cmd = parser.parse(line)
    assert first_cmd.command == command_word
    assert first_cmd.args == args_words
    assert_single_cmd(first_cmd)
    assert_descriptors(first_cmd)


@pytest.mark.parametrize("line,command_word,args_words", (
    ("cmd 1", Word("cmd"), (Word("1"),)),
    ("cmd 1 arg2", Word("cmd"), (Word("1"), Word("arg2"))),
    ("cmd 1 2", Word("cmd"), (Word("1"), Word("2"))),
    ("cmd 1 2 arg3", Word("cmd"), (Word("1"), Word("2"), Word("arg3"))),
    ("cmd 1 arg2 3", Word("cmd"), (Word("1"), Word("arg2"), Word("3"))),
    ("cmd 1 2 arg3 4", Word("cmd"), (Word("1"), Word("2"), Word("arg3"), Word("4"))),
    ("cmd 11 222", Word("cmd"), (Word("11"), Word("222"))),
    ("cmd 11 222 arg3", Word("cmd"), (Word("11"), Word("222"), Word("arg3"))),
    ("cmd 11 arg2 333", Word("cmd"), (Word("11"), Word("arg2"), Word("333"))),
    ("cmd 11 222 arg3 4444", Word("cmd"), (Word("11"), Word("222"), Word("arg3"), Word("4444"))),
    ("cmd\t1", Word("cmd"), (Word("1"),)),
    ("cmd\t1\targ2", Word("cmd"), (Word("1"), Word("arg2"))),
    ("cmd\t1\t2", Word("cmd"), (Word("1"), Word("2"))),
    ("cmd\t1\t2\targ3", Word("cmd"), (Word("1"), Word("2"), Word("arg3"))),
    ("cmd\t1\targ2\t3", Word("cmd"), (Word("1"), Word("arg2"), Word("3"))),
    ("cmd\t1\t2\targ3\t4", Word("cmd"), (Word("1"), Word("2"), Word("arg3"), Word("4"))),
    ("cmd\t11\t222", Word("cmd"), (Word("11"), Word("222"))),
    ("cmd\t11\t222\targ3", Word("cmd"), (Word("11"), Word("222"), Word("arg3"))),
    ("cmd\t11\targ2\t333", Word("cmd"), (Word("11"), Word("arg2"), Word("333"))),
    ("cmd\t11\t222\targ3\t4444", Word("cmd"), (Word("11"), Word("222"), Word("arg3"), Word("4444"))),
))
def test_multiple_word_with_numeric_only_args(parser: Parser, line: str, command_word: Word, args_words: Tuple[Word, ...]):
    first_cmd = parser.parse(line)
    assert first_cmd.command == command_word
    assert first_cmd.args == args_words
    assert_single_cmd(first_cmd)
    assert_descriptors(first_cmd)


@pytest.mark.parametrize("line,expected_str", (
    ("cmd1 arg\\ 1", "cmd1 'arg 1'"),
    ("cmd\\ 1", "'cmd 1'"),
    ("cmd\\ 1a", "'cmd 1a'"),
    ("cmd\\ 12", "'cmd 12'"),
    ("cmd\\ 12a", "'cmd 12a'"),
    ("cmd\\ 12ab", "'cmd 12ab'"),
    ("cmd\\ 1 arg\\ 1\\ arg\\ 2", "'cmd 1' 'arg 1 arg 2'"),
    ("cmd\\ 1\\;", "'cmd 1;'"),
    ("cmd\\;\\ 1 arg\\$1", "'cmd; 1' 'arg$1'"),
    ("cmd1 \\\\arg1", "cmd1 '\\arg1'"),
    ("cmd1 \\'", "cmd1 ''\"'\"''"),
    ("cmd1 \\'\"\"", "cmd1 ''\"'\"''"),
    ("cmd1 \"\"\\'", "cmd1 ''\"'\"''"),
    ("cmd1 \\\"", "cmd1 '\"'"),
    ("cmd1 \\\"''", "cmd1 '\"'"),
    ("cmd1 ''\\\"", "cmd1 '\"'"),
    ("cmd1 \\>", "cmd1 '>'"),
    ("cmd1 \\>\\>", "cmd1 '>>'"),
    ("cmd1 \\> arg2", "cmd1 '>' arg2"),
    ("cmd1 \\>\\> arg2", "cmd1 '>>' arg2"),
    ("cmd1 \\>arg1", "cmd1 '>arg1'"),
    ("cmd1 \\>\\ arg1", "cmd1 '> arg1'"),
    ("cmd1 \\>\\>arg1", "cmd1 '>>arg1'"),
    ("cmd1 \\<", "cmd1 '<'"),
    ("cmd1 \\< arg2", "cmd1 '<' arg2"),
    ("cmd1 \\<arg1", "cmd1 '<arg1'"),
    ("cmd1 \\<\\ arg1", "cmd1 '< arg1'"),
    ("cmd1 \\&", "cmd1 '&'"),
    ("cmd1 \\&\\&", "cmd1 '&&'"),
    ("cmd1 \\& arg2", "cmd1 '&' arg2"),
    ("cmd1 \\&\\& arg2", "cmd1 '&&' arg2"),
    ("cmd1 \\&\\ arg2", "cmd1 '& arg2'"),
    ("cmd1 \\&\\&\\ arg2", "cmd1 '&& arg2'"),
    ("cmd1 \\|", "cmd1 '|'"),
    ("cmd1 \\|\\|", "cmd1 '||'"),
    ("cmd1 \\| arg2", "cmd1 '|' arg2"),
    ("cmd1 \\|\\| arg2", "cmd1 '||' arg2"),
    ("cmd1 \\|\\ arg2", "cmd1 '| arg2'"),
    ("cmd1 \\|\\|\\ arg2", "cmd1 '|| arg2'"),
))
def test_escaping_outside_quotes(parser: Parser, line: str, expected_str: str):
    def check(_line: str):
        first_cmd = parser.parse(_line)
        assert str(first_cmd) == expected_str
        assert_single_cmd(first_cmd)
        assert first_cmd.asynchronous is False

    check(line)
    check(line + ";")


@pytest.mark.parametrize("line,expected_str", (
    ("cmd1 'arg\\1'", "cmd1 'arg\\1'"),
    ("cmd1 '$arg1'", "cmd1 '$arg1'"),
    ("cmd1 '\\$arg1'", "cmd1 '\\$arg1'"),
    ("cmd1 '\\a'", "cmd1 '\\a'"),
    ("cmd1 '\\'a", "cmd1 '\\a'"),
    ("cmd1 ' \\\" '", "cmd1 ' \\\" '"),
    ("cmd1 '\\\\'", "cmd1 '\\\\'"),
))
def test_escaping_inside_single_quotes(parser: Parser, line: str, expected_str: str):
    first_cmd = parser.parse(line)
    assert str(first_cmd) == expected_str
    assert_single_cmd(first_cmd)
    assert first_cmd.asynchronous is False


@pytest.mark.parametrize("line,expected_str", (
    ('cmd1 "arg\\1"', "cmd1 'arg\\1'"),
    ('cmd1 "arg\\"1"', "cmd1 'arg\"1'"),
    ('cmd1 "arg\\"\\$\\1"', "cmd1 'arg\"$\\1'"),
    ('cmd1 "arg1" "arg\\\\2"', "cmd1 arg1 'arg\\2'"),
))
def test_escaping_inside_double_quotes(parser: Parser, line: str, expected_str: str):
    first_cmd = parser.parse(line)
    assert str(first_cmd) == expected_str
    assert_single_cmd(first_cmd)
    assert first_cmd.asynchronous is False


@pytest.mark.parametrize("line,expected_str", (
    ("cmd1 'a'\"b\"", "cmd1 ab"),
    ("cmd1 \"ab \"' cd'", "cmd1 'ab  cd'"),
    ("cmd1 'a b '\"c d\"", "cmd1 'a b c d'"),
    ("cmd1 \"abc \"\\ ' def'", "cmd1 'abc   def'"),
))
def test_mixing_quotes(parser: Parser, line: str, expected_str: str):
    first_cmd = parser.parse(line)
    assert str(first_cmd) == expected_str
    assert_single_cmd(first_cmd)
    assert first_cmd.asynchronous is False


@pytest.mark.parametrize("line", (
    "cmd1 \"\"",
    "cmd1 ''",
    "cmd1 \"\"''",
    "cmd1 ''\"\"",
    "cmd1 \"\"''\"\"''",
    "cmd1 ''\"\"''\"\"",
    "cmd1 ''''",
    "cmd1 \"\"\"\"",
    "cmd1 ''''''",
    "cmd1 \"\"\"\"\"\"",
))
def test_empty_string_args(parser: Parser, line: str):
    first_cmd = parser.parse(line)
    assert str(first_cmd) == "cmd1 ''"
    assert_single_cmd(first_cmd)
    assert_descriptors(first_cmd)
    assert first_cmd.asynchronous is False


@pytest.mark.parametrize("line,expected_cmd_count,expected_strs", (
    ("cmd1 arg1; cmd2 arg1", 2, ("cmd1 arg1", "cmd2 arg1")),
    ("cmd1  arg1 ; cmd2 'arg1'", 2, ("cmd1 arg1", "cmd2 arg1")),
    ("cmd1 ' arg1 ' ;cmd2 arg1", 2, ("cmd1 ' arg1 '", "cmd2 arg1")),
    ("cmd1 arg1;cmd2 arg1", 2, ("cmd1 arg1", "cmd2 arg1")),
    ("cmd1 'arg1';cmd2 arg1", 2, ("cmd1 arg1", "cmd2 arg1")),
    ("cmd1 arg1;cmd2 'arg1'", 2, ("cmd1 arg1", "cmd2 arg1")),
    ("cmd1;cmd2;cmd3", 3, ("cmd1", "cmd2", "cmd3")),
    ("'cmd1';'cmd2';'cmd3'", 3, ("cmd1", "cmd2", "cmd3")),
    ("'cmd1' 'arg1';'cmd2' 'arg2';'cmd3' 'arg3'", 3, ("cmd1 arg1", "cmd2 arg2", "cmd3 arg3")),
    ("cmd1; cmd2; cmd3", 3, ("cmd1", "cmd2", "cmd3")),
    ("'cmd1'; 'cmd2'; 'cmd3'", 3, ("cmd1", "cmd2", "cmd3")),
    ("'cmd1';' cmd2';' cmd3'", 3, ("cmd1", "' cmd2'", "' cmd3'")),
    ("cmd1; cmd2;", 2, ("cmd1", "cmd2")),
    ("cmd1 'arg;1'; cmd2 \";;;'\" ;", 2, ("cmd1 'arg;1'", "cmd2 ';;;'\"'\"''")),
))
def test_multiple_plain_commands(parser: Parser, formatter: Formatter, line: str, expected_cmd_count: int, expected_strs: Tuple[str, ...]):
    first_cmd = parser.parse(line)
    assert first_cmd.next_command_operator is None
    assert_descriptors(first_cmd)

    actual_cmd_count = 1
    cur_cmd = first_cmd.next_command
    while cur_cmd:
        actual_cmd_count += 1
        assert cur_cmd.next_command_operator is None
        assert_descriptors(cur_cmd)

        cur_cmd = cur_cmd.next_command

    assert actual_cmd_count == expected_cmd_count

    formatted_statements = formatter.format_statements(first_cmd)
    assert formatted_statements == expected_strs
    assert len(formatted_statements) == expected_cmd_count


@pytest.mark.parametrize("line,expected_cmd_count,expected_str", (
    ("cmd1 | cmd2", 2, "cmd1 | cmd2"),
    ("cmd1 arg1 | cmd2 arg1", 2, "cmd1 arg1 | cmd2 arg1"),
    ("'cmd1' arg1 | cmd2 'arg1' | cmd3", 3, "cmd1 arg1 | cmd2 arg1 | cmd3"),
    ("cmd1|cmd2", 2, "cmd1 | cmd2"),
    ("cmd1|cmd2|cmd3|cmd4", 4, "cmd1 | cmd2 | cmd3 | cmd4"),
    ("cmd1| cmd2| cmd3", 3, "cmd1 | cmd2 | cmd3"),
    ("cmd1 |cmd2 |cmd3 |cmd4", 4, "cmd1 | cmd2 | cmd3 | cmd4"),
    ("cmd1| cmd2 |cmd3", 3, "cmd1 | cmd2 | cmd3"),
))
def test_pipe_commands(parser: Parser, formatter: Formatter, line: str, expected_cmd_count: int, expected_str: str):
    first_cmd = parser.parse(line)
    assert first_cmd.next_command is None
    assert first_cmd.next_command_operator is None
    assert_descriptors(first_cmd)

    actual_cmd_count = 1
    cur_cmd = first_cmd.pipe_command
    while cur_cmd:
        actual_cmd_count += 1
        assert cur_cmd.next_command is None
        assert cur_cmd.next_command_operator is None
        assert_descriptors(cur_cmd)

        cur_cmd = cur_cmd.pipe_command

    assert actual_cmd_count == expected_cmd_count
    assert formatter.format_statement(first_cmd) == expected_str


@pytest.mark.parametrize("space_count", range(-1, 4))
@pytest.mark.parametrize("line,expected_str", (
    ("cmd1 > testfile.txt", "cmd1 > testfile.txt"),
    ("cmd1 arg1 > testfile.txt", "cmd1 arg1 > testfile.txt"),
    ("cmd1 arg1 > 'testfile.txt'", "cmd1 arg1 > testfile.txt"),
    ("cmd1 arg1 'arg2' > testfile.txt", "cmd1 arg1 arg2 > testfile.txt"),
    ("'cmd1' arg1 > testfile.txt", "cmd1 arg1 > testfile.txt"),
    ("cmd1 'arg1 arg2' > testfile.txt", "cmd1 'arg1 arg2' > testfile.txt"),
    ("cmd1 'arg1' 'arg2' > 'testfile.txt'", "cmd1 arg1 arg2 > testfile.txt"),
    ("cmd1 'arg1' > \"testfile.txt\"", "cmd1 arg1 > testfile.txt"),
    ("cmd1 > testfile.txt arg1", "cmd1 arg1 > testfile.txt"),
    ("cmd1 > 'testfile.txt'", "cmd1 > testfile.txt"),
    ("> testfile.txt cmd1 arg1", "cmd1 arg1 > testfile.txt"),
    ("> \"testfile.txt\" cmd1 'arg1 arg2'", "cmd1 'arg1 arg2' > testfile.txt"),
    ("cmd1 'arg1 arg2'>testfile.txt", "cmd1 'arg1 arg2' > testfile.txt"),
    ("cmd1 'arg1 arg2'xx>testfile.txt", "cmd1 'arg1 arg2xx' > testfile.txt"),
    ("cmd1 'arg1 arg2'3>testfile.txt", "cmd1 'arg1 arg23' > testfile.txt"),
    ("cmd1 'arg1 arg2'>testfile.txt arg3", "cmd1 'arg1 arg2' arg3 > testfile.txt"),
    ("cmd1 'arg1 arg2'xx>testfile.txt arg3", "cmd1 'arg1 arg2xx' arg3 > testfile.txt"),
    ("cmd1 'arg1 arg2'3>testfile.txt arg3", "cmd1 'arg1 arg23' arg3 > testfile.txt"),
    ("cmd1 arg1\\>>testfile.txt", "cmd1 'arg1>' > testfile.txt"),
))
def test_redirect_output(parser: Parser, line: str, expected_str: str, space_count: int):
    file_descriptor = make_descriptor(File("testfile.txt"), RedirectionOutput())

    def check(_line: str):
        first_cmd = parser.parse(_line)
        assert_single_cmd(first_cmd)

        assert str(first_cmd.command) == "cmd1"
        assert str(first_cmd) == expected_str
        assert_descriptors(first_cmd, files={1: file_descriptor})

    if space_count < 0:
        check(line)
        return

    spaces = " " * space_count
    check(line.replace("> ", spaces + ">" + spaces))
    check(line.replace(" >", spaces + ">" + spaces))
    check(line.replace(" > ", spaces + ">" + spaces))


@pytest.mark.parametrize("space_count", range(-1, 4))
@pytest.mark.parametrize("line,expected_str", (
    ("cmd1 >> testfile.txt", "cmd1 >> testfile.txt"),
    ("cmd1 arg1 >> testfile.txt", "cmd1 arg1 >> testfile.txt"),
    ("cmd1 arg1 >> 'testfile.txt'", "cmd1 arg1 >> testfile.txt"),
    ("cmd1 arg1 'arg2' >> testfile.txt", "cmd1 arg1 arg2 >> testfile.txt"),
    ("'cmd1' arg1 >> testfile.txt", "cmd1 arg1 >> testfile.txt"),
    ("cmd1 'arg1 arg2' >> testfile.txt", "cmd1 'arg1 arg2' >> testfile.txt"),
    ("cmd1 'arg1' 'arg2' >> 'testfile.txt'", "cmd1 arg1 arg2 >> testfile.txt"),
    ("cmd1 'arg1' >> \"testfile.txt\"", "cmd1 arg1 >> testfile.txt"),
    ("cmd1 >> testfile.txt arg1", "cmd1 arg1 >> testfile.txt"),
    ("cmd1 >> 'testfile.txt'", "cmd1 >> testfile.txt"),
    (">> testfile.txt cmd1 arg1", "cmd1 arg1 >> testfile.txt"),
    (">> \"testfile.txt\" cmd1 'arg1 arg2'", "cmd1 'arg1 arg2' >> testfile.txt"),
    ("cmd1 'arg1 arg2'>>testfile.txt", "cmd1 'arg1 arg2' >> testfile.txt"),
    ("cmd1 'arg1 arg2'xx>>testfile.txt", "cmd1 'arg1 arg2xx' >> testfile.txt"),
    ("cmd1 'arg1 arg2'3>>testfile.txt", "cmd1 'arg1 arg23' >> testfile.txt"),
    ("cmd1 'arg1 arg2'>>testfile.txt arg3", "cmd1 'arg1 arg2' arg3 >> testfile.txt"),
    ("cmd1 'arg1 arg2'xx>>testfile.txt arg3", "cmd1 'arg1 arg2xx' arg3 >> testfile.txt"),
    ("cmd1 'arg1 arg2'3>>testfile.txt arg3", "cmd1 'arg1 arg23' arg3 >> testfile.txt"),
    ("cmd1 arg1\\> >>testfile.txt", "cmd1 'arg1>' >> testfile.txt"),
    ("cmd1 arg1\\>\\> >> testfile.txt", "cmd1 'arg1>>' >> testfile.txt"),
))
def test_redirect_append(parser: Parser, line: str, expected_str: str, space_count: int):
    file_descriptor = make_descriptor(File("testfile.txt"), RedirectionAppend())

    def check(_line: str):
        first_cmd = parser.parse(line)
        assert_single_cmd(first_cmd)

        assert str(first_cmd.command) == "cmd1"
        assert str(first_cmd) == expected_str
        assert_descriptors(first_cmd, files={1: file_descriptor})

    if space_count < 0:
        check(line)
        return

    spaces = " " * space_count
    check(line.replace(">> ", spaces + ">>" + spaces))
    check(line.replace(" >>", spaces + ">>" + spaces))
    check(line.replace(" >> ", spaces + ">>" + spaces))


@pytest.mark.parametrize("pipe_space_count", range(-1, 4))
@pytest.mark.parametrize("redirect_space_count", range(0, 4))
@pytest.mark.parametrize("pipe_chars", ("| ", " |", " | "))
@pytest.mark.parametrize("line,expected_str", (
    ("cmd1 arg1 > file1.txt | cmd2 arg1 arg2", "cmd1 arg1 > file1.txt | cmd2 arg1 arg2"),
    ("cmd1 'arg1 arg2' > file1.txt | cmd2 arg1 ", "cmd1 'arg1 arg2' > file1.txt | cmd2 arg1"),
    ("> file1.txt cmd1 | cmd2  \"arg1 arg2 \"", "cmd1 > file1.txt | cmd2 'arg1 arg2 '"),
))
def test_pipe_and_redirect_output_left_side_only(parser: Parser, formatter: Formatter, line: str, expected_str: str, pipe_space_count: int, redirect_space_count: int, pipe_chars: str):
    file_descriptor = make_descriptor(File("file1.txt"), RedirectionOutput())

    def check(_line):
        first_cmd = parser.parse(_line)
        assert first_cmd.next_command is None
        assert first_cmd.next_command_operator is None
        assert formatter.format_statement(first_cmd) == expected_str

        assert str(first_cmd.command) == "cmd1"
        assert isinstance(first_cmd.pipe_command, Command)
        assert_descriptors(first_cmd, files={1: file_descriptor})

        second_cmd = first_cmd.pipe_command
        assert str(second_cmd.command).strip() == "cmd2"
        assert_single_cmd(second_cmd)
        assert_descriptors(second_cmd)

    if pipe_space_count < 0:
        check(line)
        return

    pipe_spaces = " " * pipe_space_count
    redirect_spaces = " " * redirect_space_count
    redirect_chars = redirect_spaces + ">" + redirect_spaces

    _line = line.replace(pipe_chars, pipe_spaces + "|" + pipe_spaces)
    check(_line)
    check(_line.replace("> ", redirect_chars))
    check(_line.replace(" >", redirect_chars))
    check(_line.replace(" > ", redirect_chars))


@pytest.mark.parametrize("pipe_space_count", range(-1, 4))
@pytest.mark.parametrize("redirect_space_count", range(0, 4))
@pytest.mark.parametrize("pipe_chars", ("| ", " |", " | "))
@pytest.mark.parametrize("line,expected_str", (
    ("'cmd1' | ' cmd2 ' > file2.txt", "cmd1 | ' cmd2 ' > file2.txt"),
    ("cmd1 | > file2.txt cmd2 arg1", "cmd1 | cmd2 arg1 > file2.txt"),
    ("cmd1 | cmd2 2arg > file2.txt", "cmd1 | cmd2 2arg > file2.txt"),
))
def test_pipe_and_redirect_output_right_side_only(parser: Parser, formatter: Formatter, line: str, expected_str: str, pipe_space_count: int, redirect_space_count: int, pipe_chars: str):
    file_descriptor = make_descriptor(File("file2.txt"), RedirectionOutput())

    def check(_line):
        first_cmd = parser.parse(_line)
        assert first_cmd.next_command is None
        assert first_cmd.next_command_operator is None
        assert formatter.format_statement(first_cmd) == expected_str

        assert str(first_cmd.command) == "cmd1"
        assert isinstance(first_cmd.pipe_command, Command)
        assert_descriptors(first_cmd)

        second_cmd = first_cmd.pipe_command
        assert str(second_cmd.command).strip() == "cmd2"
        assert_single_cmd(second_cmd)
        assert_descriptors(second_cmd, files={1: file_descriptor})

    if pipe_space_count < 0:
        check(line)
        return

    pipe_spaces = " " * pipe_space_count
    redirect_spaces = " " * redirect_space_count
    redirect_chars = redirect_spaces + ">" + redirect_spaces

    _line = line.replace(pipe_chars, pipe_spaces + "|" + pipe_spaces)
    check(_line)
    check(_line.replace("> ", redirect_chars))
    check(_line.replace(" >", redirect_chars))
    check(_line.replace(" > ", redirect_chars))


@pytest.mark.parametrize("pipe_space_count", range(-1, 4))
@pytest.mark.parametrize("redirect_space_count", range(0, 4))
@pytest.mark.parametrize("pipe_chars", ("| ", " |", " | "))
@pytest.mark.parametrize("line,expected_str", (
    ("cmd1 arg1 > file1.txt | cmd2 arg1 arg2 > file2.txt", "cmd1 arg1 > file1.txt | cmd2 arg1 arg2 > file2.txt"),
    ("cmd1 'arg1 arg2' > file1.txt | cmd2 arg1 > file2.txt", "cmd1 'arg1 arg2' > file1.txt | cmd2 arg1 > file2.txt"),
    ("> file1.txt cmd1 | cmd2 > file2.txt \"arg1 arg2 \"", "cmd1 > file1.txt | cmd2 'arg1 arg2 ' > file2.txt"),
    ("> file1.txt 'cmd1' | ' cmd2 ' > file2.txt", "cmd1 > file1.txt | ' cmd2 ' > file2.txt"),
    ("cmd1 arg1 > file1.txt | > file2.txt cmd2", "cmd1 arg1 > file1.txt | cmd2 > file2.txt"),
))
def test_pipe_and_redirect_output_both_sides(parser: Parser, formatter: Formatter, line: str, expected_str: str, pipe_space_count: int, redirect_space_count: int, pipe_chars: str):
    file1_descriptor = make_descriptor(File("file1.txt"), RedirectionOutput())
    file2_descriptor = make_descriptor(File("file2.txt"), RedirectionOutput())

    def check(_line):
        first_cmd = parser.parse(_line)
        assert first_cmd.next_command is None
        assert first_cmd.next_command_operator is None
        assert formatter.format_statement(first_cmd) == expected_str

        assert str(first_cmd.command) == "cmd1"
        assert isinstance(first_cmd.pipe_command, Command)
        assert_descriptors(first_cmd, files={1: file1_descriptor})

        second_cmd = first_cmd.pipe_command
        assert str(second_cmd.command).strip() == "cmd2"
        assert_single_cmd(second_cmd)
        assert_descriptors(second_cmd, files={1: file2_descriptor})

    if pipe_space_count < 0:
        check(line)
        return

    pipe_spaces = " " * pipe_space_count
    redirect_spaces = " " * redirect_space_count
    redirect_chars = redirect_spaces + ">" + redirect_spaces

    _line = line.replace(pipe_chars, pipe_spaces + "|" + pipe_spaces)
    check(_line)
    check(_line.replace("> ", redirect_chars))
    check(_line.replace(" >", redirect_chars))
    check(_line.replace(" > ", redirect_chars))


@pytest.mark.parametrize("pipe_space_count", range(-1, 4))
@pytest.mark.parametrize("redirect_space_count", range(0, 4))
@pytest.mark.parametrize("pipe_chars", ("| ", " |", " | "))
@pytest.mark.parametrize("line,expected_str", (
    ("cmd1 arg1 >> file1.txt | cmd2 arg1 arg2", "cmd1 arg1 >> file1.txt | cmd2 arg1 arg2"),
    ("cmd1 'arg1 arg2' >> file1.txt | cmd2 arg1 ", "cmd1 'arg1 arg2' >> file1.txt | cmd2 arg1"),
    (">> file1.txt cmd1 | cmd2  \"arg1 arg2 \"", "cmd1 >> file1.txt | cmd2 'arg1 arg2 '"),
))
def test_pipe_and_redirect_append_left_side_only(parser: Parser, formatter: Formatter, line: str, expected_str: str, pipe_space_count: int, redirect_space_count: int, pipe_chars: str):
    file_descriptor = make_descriptor(File("file1.txt"), RedirectionAppend())

    def check(_line):
        first_cmd = parser.parse(_line)
        assert first_cmd.next_command is None
        assert first_cmd.next_command_operator is None
        assert formatter.format_statement(first_cmd) == expected_str

        assert str(first_cmd.command) == "cmd1"
        assert isinstance(first_cmd.pipe_command, Command)
        assert_descriptors(first_cmd, files={1: file_descriptor})

        second_cmd = first_cmd.pipe_command
        assert str(second_cmd.command).strip() == "cmd2"
        assert_single_cmd(second_cmd)
        assert_descriptors(second_cmd)

    if pipe_space_count < 0:
        check(line)
        return

    pipe_spaces = " " * pipe_space_count
    redirect_spaces = " " * redirect_space_count
    redirect_chars = redirect_spaces + ">>" + redirect_spaces

    _line = line.replace(pipe_chars, pipe_spaces + "|" + pipe_spaces)
    check(_line)
    check(_line.replace(">> ", redirect_chars))
    check(_line.replace(" >>", redirect_chars))
    check(_line.replace(" >> ", redirect_chars))


@pytest.mark.parametrize("pipe_space_count", range(-1, 4))
@pytest.mark.parametrize("redirect_space_count", range(0, 4))
@pytest.mark.parametrize("pipe_chars", ("| ", " |", " | "))
@pytest.mark.parametrize("line,expected_str", (
    ("'cmd1' | ' cmd2 ' >> file2.txt", "cmd1 | ' cmd2 ' >> file2.txt"),
    ("cmd1 | >> file2.txt cmd2 arg1", "cmd1 | cmd2 arg1 >> file2.txt"),
    ("cmd1 | cmd2 2arg >> file2.txt", "cmd1 | cmd2 2arg >> file2.txt"),
))
def test_pipe_and_redirect_append_right_side_only(parser: Parser, formatter: Formatter, line: str, expected_str: str, pipe_space_count: int, redirect_space_count: int, pipe_chars: str):
    file_descriptor = make_descriptor(File("file2.txt"), RedirectionAppend())

    def check(_line):
        first_cmd = parser.parse(_line)
        assert first_cmd.next_command is None
        assert first_cmd.next_command_operator is None
        assert formatter.format_statement(first_cmd) == expected_str

        assert str(first_cmd.command) == "cmd1"
        assert isinstance(first_cmd.pipe_command, Command)
        assert_descriptors(first_cmd)

        second_cmd = first_cmd.pipe_command
        assert str(second_cmd.command).strip() == "cmd2"
        assert_single_cmd(second_cmd)
        assert_descriptors(second_cmd, files={1: file_descriptor})

    if pipe_space_count < 0:
        check(line)
        return

    pipe_spaces = " " * pipe_space_count
    redirect_spaces = " " * redirect_space_count
    redirect_chars = redirect_spaces + ">>" + redirect_spaces

    _line = line.replace(pipe_chars, pipe_spaces + "|" + pipe_spaces)
    check(_line)
    check(_line.replace(">> ", redirect_chars))
    check(_line.replace(" >>", redirect_chars))
    check(_line.replace(" >> ", redirect_chars))


@pytest.mark.parametrize("pipe_space_count", range(-1, 4))
@pytest.mark.parametrize("redirect_space_count", range(0, 4))
@pytest.mark.parametrize("pipe_chars", ("| ", " |", " | "))
@pytest.mark.parametrize("line,expected_str", (
    ("cmd1 arg1 >> file1.txt | cmd2 arg1 arg2 >> file2.txt", "cmd1 arg1 >> file1.txt | cmd2 arg1 arg2 >> file2.txt"),
    ("cmd1 'arg1 arg2' >> file1.txt | cmd2 arg1 >> file2.txt", "cmd1 'arg1 arg2' >> file1.txt | cmd2 arg1 >> file2.txt"),
    (">> file1.txt cmd1 | cmd2 >> file2.txt \"arg1 arg2 \"", "cmd1 >> file1.txt | cmd2 'arg1 arg2 ' >> file2.txt"),
    (">> file1.txt 'cmd1' | ' cmd2 ' >> file2.txt", "cmd1 >> file1.txt | ' cmd2 ' >> file2.txt"),
    ("cmd1 arg1 >> file1.txt | >> file2.txt cmd2", "cmd1 arg1 >> file1.txt | cmd2 >> file2.txt"),
))
def test_pipe_and_redirect_append_both_sides(parser: Parser, formatter: Formatter, line: str, expected_str: str, pipe_space_count: int, redirect_space_count: int, pipe_chars: str):
    file1_descriptor = make_descriptor(File("file1.txt"), RedirectionAppend())
    file2_descriptor = make_descriptor(File("file2.txt"), RedirectionAppend())

    def check(_line):
        first_cmd = parser.parse(_line)
        assert first_cmd.next_command is None
        assert first_cmd.next_command_operator is None
        assert formatter.format_statement(first_cmd) == expected_str

        assert str(first_cmd.command) == "cmd1"
        assert isinstance(first_cmd.pipe_command, Command)
        assert_descriptors(first_cmd, files={1: file1_descriptor})

        second_cmd = first_cmd.pipe_command
        assert str(second_cmd.command).strip() == "cmd2"
        assert_single_cmd(second_cmd)
        assert_descriptors(second_cmd, files={1: file2_descriptor})

    if pipe_space_count < 0:
        check(line)
        return

    pipe_spaces = " " * pipe_space_count
    redirect_spaces = " " * redirect_space_count
    redirect_chars = redirect_spaces + ">>" + redirect_spaces

    _line = line.replace(pipe_chars, pipe_spaces + "|" + pipe_spaces)
    check(_line)
    check(_line.replace(">> ", redirect_chars))
    check(_line.replace(" >>", redirect_chars))
    check(_line.replace(" >> ", redirect_chars))


def test_duplicating_descriptors(parser: Parser):
    stmt1 = "cmd arg1 >&2"
    cmd1 = parser.parse(stmt1)
    assert_single_cmd(cmd1)
    assert_descriptors(cmd1, files={1: DEFAULT_DESCRIPTOR_STDERR})
    assert str(cmd1) == "cmd arg1 > /dev/stderr"

    stmt2 = "cmd arg1 >&2 2>&-"
    cmd2 = parser.parse(stmt2)
    assert_single_cmd(cmd2)
    assert_descriptors(cmd2, files={1: DEFAULT_DESCRIPTOR_STDERR}, closed=frozenset((2,)))
    assert str(cmd2) == "cmd arg1 > /dev/stderr 2>&-"

    stmt3 = "cmd 'arg1 arg2'2>&-"
    cmd3 = parser.parse(stmt3)
    assert_single_cmd(cmd3)
    assert_descriptors(cmd3, closed=frozenset((1,)))
    assert str(cmd3) == "cmd 'arg1 arg22' >&-"

    stmt4 = "cmd arg1 2>&1"
    cmd4 = parser.parse(stmt4)
    assert_single_cmd(cmd4)
    assert_descriptors(cmd4, files={2: DEFAULT_DESCRIPTOR_STDOUT})
    assert str(cmd4) == "cmd arg1 2> /dev/stdout"

    stmt5 = "cmd arg1 22>&2 >33 44>&22"
    cmd5 = parser.parse(stmt5)
    assert_single_cmd(cmd5)
    cmd5_files = {
        1: make_descriptor(File(name="33"), RedirectionOutput()),
        22: DEFAULT_DESCRIPTOR_STDERR,
        44: DEFAULT_DESCRIPTOR_STDERR,
    }
    assert_descriptors(cmd5, files=cmd5_files)
    assert str(cmd5) == "cmd arg1 > 33 22> /dev/stderr 44> /dev/stderr"

    stmt6 = "cmd arg1 22>&2 >44 44>&22 2>&-"
    cmd6 = parser.parse(stmt6)
    assert_single_cmd(cmd6)
    cmd6_files = {
        1: make_descriptor(File(name="44"), RedirectionOutput()),
        22: DEFAULT_DESCRIPTOR_STDERR,
        44: DEFAULT_DESCRIPTOR_STDERR,
    }
    assert_descriptors(cmd6, files=cmd6_files, closed=frozenset((2,)))
    assert str(cmd6) == "cmd arg1 > 44 2>&- 22> /dev/stderr 44> /dev/stderr"


@pytest.mark.parametrize("line,descriptors,expected_str", (
    ("cmd arg1 >&-", frozenset((1,)), "cmd arg1 >&-"),
    ("cmd arg1 >& -", frozenset((1,)), "cmd arg1 >&-"),
    ("cmd arg1 2>&- >&-", frozenset((1, 2)), "cmd arg1 >&- 2>&-"),
    ("cmd 'arg1 arg2'>&-", frozenset((1,)), "cmd 'arg1 arg2' >&-"),
    ("cmd 'arg1 arg2'2>&-", frozenset((1,)), "cmd 'arg1 arg22' >&-"),
    ("cmd 'arg1 arg2'2>&-2>&-", frozenset((1, 2)), "cmd 'arg1 arg22' >&- 2>&-"),
    ("cmd 'arg1 arg2'>&- arg3", frozenset((1,)), "cmd 'arg1 arg2' arg3 >&-"),
    ("cmd 'arg1 arg2'2>&- arg3", frozenset((1,)), "cmd 'arg1 arg22' arg3 >&-"),
    ("cmd 'arg1 arg2'2>&-2>&- arg3", frozenset((1, 2)), "cmd 'arg1 arg22' arg3 >&- 2>&-"),
    ("cmd arg1 arg2>&-", frozenset((1,)), "cmd arg1 arg2 >&-"),
    ("cmd arg1 2>&-", frozenset((2,)), "cmd arg1 2>&-"),
    ("cmd arg1 \\2>&-", frozenset((1,)), "cmd arg1 2 >&-"),
    ("cmd arg1 'arg2'>&-'arg3'", frozenset((1,)), "cmd arg1 arg2 arg3 >&-"),
    ("cmd arg1 1000>&-", frozenset((1000,)), "cmd arg1 1000>&-"),
    ("cmd arg1 >&--", frozenset((1,)), "cmd arg1 - >&-"),
))
def test_closing_descriptors(parser: Parser, line: str, descriptors: AbstractSet[int], expected_str: str):
    first_cmd = parser.parse(line)
    assert_single_cmd(first_cmd)
    assert_descriptors(first_cmd, closed=descriptors)
    assert str(first_cmd) == expected_str


@pytest.mark.parametrize("line", (
    "cmd >&a",
    "cmd >&1a",
    "cmd >&a1",
    "cmd >&1a1",
    "cmd >&a1a",
    "cmd >&\\--",
))
def test_ambiguous_descriptor_redirects(parser: Parser, line: str):
    def check(_line: str):
        with pytest.raises(AmbiguousRedirectParserFailure):
            parser.parse(_line)
        with pytest.raises(AmbiguousRedirectParserFailure):
            parser.parse(_line.replace("cmd", "cmd arg1"))
        with pytest.raises(AmbiguousRedirectParserFailure):
            parser.parse(_line.replace("cmd ", "cmd arg1"))
        with pytest.raises(AmbiguousRedirectParserFailure):
            parser.parse(_line.replace("cmd ", "cmd 'arg1'"))

    check(line)
    check(line.replace(">", "2>"))


def test_unusual_descriptor_redirects(parser: Parser):
    stmt1 = "cmd1 2>&2"
    cmd1 = parser.parse(stmt1)
    assert_single_cmd(cmd1)
    assert_descriptors(cmd1)
    assert str(cmd1) == "cmd1"

    stmt2 = "cmd2 2>test2.txt 2>&2"
    cmd2 = parser.parse(stmt2)
    assert_single_cmd(cmd2)
    assert_descriptors(cmd2, files={2: make_descriptor(File(name="test2.txt"), RedirectionOutput())})
    assert str(cmd2) == "cmd2 2> test2.txt"

    stmt3 = "cmd3 2>test3.txt2>&2"
    cmd3 = parser.parse(stmt3)
    assert_single_cmd(cmd3)
    cmd3_files = {
        1: make_descriptor(File(name="test3.txt2"), RedirectionOutput()),
        2: make_descriptor(File(name="test3.txt2"), RedirectionOutput()),
    }
    assert_descriptors(cmd3, files=cmd3_files)
    assert str(cmd3) == "cmd3 > test3.txt2 2> test3.txt2"

    stmt4 = "cmd4 \\2>test4.txt2>&2"
    cmd4 = parser.parse(stmt4)
    assert_single_cmd(cmd4)
    cmd4_files = {
        1: make_descriptor(DefaultFile(target=StderrTarget()), RedirectionOutput()),
    }
    assert_descriptors(cmd4, files=cmd4_files)
    assert str(cmd4) == "cmd4 2 > /dev/stderr"

    stmt5 = "cmd5 0> test5.txt"
    cmd5 = parser.parse(stmt5)
    assert_single_cmd(cmd5)
    assert_descriptors(cmd5, files={0: make_descriptor(File(name="test5.txt"), RedirectionOutput())})
    assert str(cmd5) == "cmd5 0> test5.txt"

    stmt6 = "cmd6 1< test6.txt"
    cmd6 = parser.parse(stmt6)
    assert_single_cmd(cmd6)
    assert_descriptors(cmd6, files={1: make_descriptor(File(name="test6.txt"), RedirectionInput())})
    assert str(cmd6) == "cmd6 1< test6.txt"

    stmt7 = "cmd7 22<&0"
    cmd7 = parser.parse(stmt7)
    assert_single_cmd(cmd7)
    assert_descriptors(cmd7, files={22: make_descriptor(DefaultFile(target=StdinTarget()), RedirectionInput())})
    assert str(cmd7) == "cmd7 22< /dev/stdin"

    stmt8 = "cmd8 2>\\-test8.txt"
    cmd8 = parser.parse(stmt8)
    assert_single_cmd(cmd8)
    assert_descriptors(cmd8, files={2: make_descriptor(File(name="-test8.txt"), RedirectionOutput())})
    assert str(cmd8) == "cmd8 2> -test8.txt"


@pytest.mark.parametrize("line,fd", (
    ("cmd > test.txt", 1),
    ("cmd 0> test.txt", 0),
    ("cmd 1> test.txt", 1),
    ("cmd 2> test.txt", 2),
    ("cmd 3> test.txt", 3),
    ("cmd 4> test.txt", 4),
    ("cmd 5> test.txt", 5),
    ("cmd 6> test.txt", 6),
    ("cmd 7> test.txt", 7),
    ("cmd 8> test.txt", 8),
    ("cmd 9> test.txt", 9),
))
def test_every_descriptor_starting_digit_redirect_output(parser: Parser, line: str, fd: int):
    file_descriptor = make_descriptor(File(name="test.txt"), RedirectionOutput())
    first_cmd = parser.parse(line)
    assert first_cmd.command == Word("cmd")
    assert_single_cmd(first_cmd)
    assert_descriptors(first_cmd, files={fd: file_descriptor})


@pytest.mark.parametrize("line,fd", (
    ("cmd >> test.txt", 1),
    ("cmd 0>> test.txt", 0),
    ("cmd 1>> test.txt", 1),
    ("cmd 2>> test.txt", 2),
    ("cmd 3>> test.txt", 3),
    ("cmd 4>> test.txt", 4),
    ("cmd 5>> test.txt", 5),
    ("cmd 6>> test.txt", 6),
    ("cmd 7>> test.txt", 7),
    ("cmd 8>> test.txt", 8),
    ("cmd 9>> test.txt", 9),
))
def test_every_descriptor_starting_digit_redirect_append(parser: Parser, line: str, fd: int):
    file_descriptor = make_descriptor(File(name="test.txt"), RedirectionAppend())
    first_cmd = parser.parse(line)
    assert first_cmd.command == Word("cmd")
    assert_single_cmd(first_cmd)
    assert_descriptors(first_cmd, files={fd: file_descriptor})


@pytest.mark.parametrize("line,fd", (
    ("cmd < test.txt", 0),
    ("cmd 0< test.txt", 0),
    ("cmd 1< test.txt", 1),
    ("cmd 2< test.txt", 2),
    ("cmd 3< test.txt", 3),
    ("cmd 4< test.txt", 4),
    ("cmd 5< test.txt", 5),
    ("cmd 6< test.txt", 6),
    ("cmd 7< test.txt", 7),
    ("cmd 8< test.txt", 8),
    ("cmd 9< test.txt", 9),
))
def test_every_descriptor_starting_digit_redirect_input(parser: Parser, line: str, fd: int):
    file_descriptor = make_descriptor(File(name="test.txt"), RedirectionInput())
    first_cmd = parser.parse(line)
    assert first_cmd.command == Word("cmd")
    assert_single_cmd(first_cmd)
    assert_descriptors(first_cmd, files={fd: file_descriptor})


@pytest.mark.parametrize("line", (
    "cmd >>&a",
    "cmd >>&1a",
    "cmd >>&a1",
    "cmd >>&1a1",
    "cmd >>&a1a",
))
def test_invalid_descriptor_duplications(parser: Parser, line: str):
    def check(_line: str):
        with pytest.raises(InvalidRedirectionParserFailure):
            parser.parse(_line)
        with pytest.raises(InvalidRedirectionParserFailure):
            parser.parse(_line.replace("cmd", "cmd arg1"))
        with pytest.raises(InvalidRedirectionParserFailure):
            parser.parse(_line.replace("cmd ", "cmd arg1"))
        with pytest.raises(InvalidRedirectionParserFailure):
            parser.parse(_line.replace("cmd ", "cmd 'arg1'"))

    check(line)
    check(line.replace(">>", "2>>"))


@pytest.mark.parametrize("line", (
    "cmd >&- 2>&1",
    "cmd 2>&- 1>&2",
    "cmd 2>&- 3>&- 4>&2",
    "cmd 3>&- 4>&3",
    "cmd 4>&3",
))
def test_bad_descriptor_duplications(parser: Parser, line: str):
    with pytest.raises(BadFileDescriptorException):
        parser.parse(line)


@pytest.mark.parametrize("pipe_space_count", range(-1, 4))
@pytest.mark.parametrize("line,expected_cmd_count,expected_str", (
    ("cmd1 arg1 | cmd2 arg1 | cmd3 arg1", 3, "cmd1 arg1 | cmd2 arg1 | cmd3 arg1"),
    ("cmd1 'arg1 arg2' | cmd2 \"arg1 arg2\" arg3", 2, "cmd1 'arg1 arg2' | cmd2 'arg1 arg2' arg3"),
    ("cmd1 'arg1' 'arg2' 'arg3' | cmd2 | cmd3 \"\\arg1'\"", 3, "cmd1 arg1 arg2 arg3 | cmd2 | cmd3 '\\arg1'\"'\"''"),
    ("cmd1 | cmd2 | cmd3 | cmd4 | cmd5", 5, "cmd1 | cmd2 | cmd3 | cmd4 | cmd5"),
))
def test_multiple_pipes(parser: Parser, formatter: Formatter, line: str, expected_cmd_count: int, expected_str: str, pipe_space_count: int):
    def check(_line):
        first_cmd = parser.parse(_line)
        assert first_cmd.next_command is None
        assert first_cmd.next_command_operator is None
        assert_descriptors(first_cmd)

        actual_cmd_count = 1
        cur_cmd = first_cmd.pipe_command
        while cur_cmd:
            actual_cmd_count += 1
            assert cur_cmd.next_command is None
            assert cur_cmd.next_command_operator is None
            assert_descriptors(cur_cmd)
            cur_cmd = cur_cmd.pipe_command

        assert actual_cmd_count == expected_cmd_count

        actual_str = formatter.format_statement(first_cmd)
        assert actual_str == expected_str

    if pipe_space_count < 0:
        check(line)
        return

    pipe_spaces = " " * pipe_space_count
    pipe_space_chars = pipe_spaces + "|" + pipe_spaces
    check(line.replace("| ", pipe_space_chars))
    check(line.replace(" |", pipe_space_chars))
    check(line.replace(" | ", pipe_space_chars))


@pytest.mark.parametrize("line,expected_stmt_count,expected_cmd_count", (
    ("cmd1 | cmd2; cmd3", 2, 3),
    ("cmd1 arg1 | cmd2 arg1 'arg2 arg3' | cmd3 arg1; cmd4 arg1 | cmd5 arg1", 2, 5),
    ("cmd1; cmd2; cmd3 | cmd4", 3, 4),
    ("cmd1; cmd2 | cmd3; cmd4", 3, 4),
    ("cmd1 | cmd2; cmd3; cmd4; cmd5 | cmd6", 4, 6),
    ("cmd1 | cmd2 | cmd3; cmd4 | cmd5 | cmd6 | cmd7; cmd8; cmd9", 4, 9),
))
def test_multiple_statements_with_pipes(parser: Parser, line: str, expected_stmt_count: int, expected_cmd_count: int):
    def check(_line: str):
        first_cmd = parser.parse(_line)
        stmt_count = 0
        cmd_count = 0
        cur_cmd = first_cmd
        cur_pipe_cmd = first_cmd
        while cur_cmd:
            assert cur_cmd.next_command_operator is None
            assert_descriptors(cur_cmd)
            stmt_count += 1
            cmd_count += 1
            cur_pipe_cmd = cur_cmd.pipe_command
            while cur_pipe_cmd:
                assert cur_pipe_cmd.next_command is None
                assert cur_pipe_cmd.next_command_operator is None
                assert_descriptors(cur_pipe_cmd)
                cmd_count += 1
                cur_pipe_cmd = cur_pipe_cmd.pipe_command
            cur_cmd = cur_cmd.next_command

        assert stmt_count == expected_stmt_count
        assert cmd_count == expected_cmd_count

    check(line)
    check(line + ";")


@pytest.mark.parametrize("line,expected_cmd_count,expected_str", (
    ("cmd1 && cmd2", 2, "cmd1 && cmd2"),
    ("cmd1 && cmd2 && cmd3", 3, "cmd1 && cmd2 && cmd3"),
    ("cmd1 arg1 && cmd2", 2, "cmd1 arg1 && cmd2"),
    ("cmd1 \\&\\&arg1 && cmd2 && cmd3 'arg1'", 3, "cmd1 '&&arg1' && cmd2 && cmd3 arg1"),
    ("cmd1 'arg 1' && cmd2 'arg 1' && cmd3", 3, "cmd1 'arg 1' && cmd2 'arg 1' && cmd3"),
    ("cmd1 && cmd2 && cmd3 && cmd4 arg1", 4, "cmd1 && cmd2 && cmd3 && cmd4 arg1"),
    ("cmd1 > file1.txt && cmd2", 2, "cmd1 > file1.txt && cmd2"),
    ("cmd1 arg1 && cmd2 > file2.txt", 2, "cmd1 arg1 && cmd2 > file2.txt"),
    ("cmd1 > file1.txt && cmd2 > file2.txt && cmd3 > file3.txt", 3, "cmd1 > file1.txt && cmd2 > file2.txt && cmd3 > file3.txt"),
    ("cmd1 \"arg1&&arg2\" && cmd2 > 'file2.txt'", 2, "cmd1 'arg1&&arg2' && cmd2 > file2.txt"),
))
def test_anded_statements(parser: Parser, formatter: Formatter, line: str, expected_cmd_count: int, expected_str: str):
    def check(_line: str):
        first_cmd = parser.parse(_line)
        cur_cmd = first_cmd.next_command
        cmd_count = 1
        while cur_cmd:
            cur_cmd = cur_cmd.next_command
            cmd_count += 1
        assert cmd_count == expected_cmd_count

        formatted_statements = formatter.format_statements(first_cmd)
        assert len(formatted_statements) == 1
        assert formatted_statements[0] == expected_str

    check(line)
    check(line.replace("&& ", "&&"))
    check(line.replace(" &&", "&&"))
    check(line.replace(" && ", "&&"))


@pytest.mark.parametrize("line,expected_cmd_count,expected_str", (
    ("cmd1 || cmd2", 2, "cmd1 || cmd2"),
    ("cmd1 || cmd2 || cmd3", 3, "cmd1 || cmd2 || cmd3"),
    ("cmd1 arg1 || cmd2", 2, "cmd1 arg1 || cmd2"),
    ("cmd1 \\|\\|arg1 || cmd2 || cmd3 'arg1'", 3, "cmd1 '||arg1' || cmd2 || cmd3 arg1"),
    ("cmd1 'arg 1' || cmd2 'arg 1' || cmd3", 3, "cmd1 'arg 1' || cmd2 'arg 1' || cmd3"),
    ("cmd1 || cmd2 || cmd3 || cmd4 arg1", 4, "cmd1 || cmd2 || cmd3 || cmd4 arg1"),
    ("cmd1 > file1.txt || cmd2", 2, "cmd1 > file1.txt || cmd2"),
    ("cmd1 arg1 || cmd2 > file2.txt", 2, "cmd1 arg1 || cmd2 > file2.txt"),
    ("cmd1 > file1.txt || cmd2 > file2.txt || cmd3 > file3.txt", 3, "cmd1 > file1.txt || cmd2 > file2.txt || cmd3 > file3.txt"),
    ("cmd1 \"arg1||arg2\" || cmd2 > 'file2.txt'", 2, "cmd1 'arg1||arg2' || cmd2 > file2.txt"),
))
def test_ored_statements(parser: Parser, formatter: Formatter, line: str, expected_cmd_count: int, expected_str: str):
    def check(_line: str):
        first_cmd = parser.parse(_line)
        cur_cmd = first_cmd.next_command
        cmd_count = 1
        while cur_cmd:
            cur_cmd = cur_cmd.next_command
            cmd_count += 1
        assert cmd_count == expected_cmd_count

        formatted_statements = formatter.format_statements(first_cmd)
        assert len(formatted_statements) == 1
        assert formatted_statements[0] == expected_str

    check(line)
    check(line.replace("|| ", "||"))
    check(line.replace(" ||", "||"))
    check(line.replace(" || ", "||"))


@pytest.mark.parametrize("line,expected_cmd_count", (
    ("cmd1 arg1 && cmd2 arg2 || cmd3 arg3", 3),
    ("cmd1 arg1 arg2 || cmd2 'arg3 arg4' && cmd3", 3),
    ("cmd1 'arg1 arg2' && cmd2; cmd3 || cmd4", 4),
    ("cmd1 arg1 || cmd2; cmd3", 3),
    ("cmd1 arg1; cmd2 && cmd3 arg2", 3),
    ("cmd1 arg1; cmd2 || cmd3 arg2", 3),
    ("cmd1 arg1; cmd2 && cmd3 arg2 || cmd4 'arg3 arg4'", 4),
    ("cmd1 arg1; cmd2 || cmd3 arg2 && cmd4 'arg3 arg4'; cmd5 && cmd6 arg5", 6),
))
def test_mixed_next_cmd_operators(parser: Parser, formatter: Formatter, line: str, expected_cmd_count: int):
    first_cmd = parser.parse(line)
    assert_descriptors(first_cmd)
    formatted_statements = formatter.format_statements(first_cmd)
    assert "; ".join(formatted_statements) == line

    cur_cmd = first_cmd.next_command
    cmd_count = 1
    while cur_cmd:
        assert_descriptors(cur_cmd)
        cur_cmd = cur_cmd.next_command
        cmd_count += 1
    assert cmd_count == expected_cmd_count


def test_mixed_next_cmd_operators_with_pipes1(parser: Parser, formatter: Formatter):
    first_cmd = parser.parse("cmd1 | cmd2 && cmd3 || cmd4")
    assert first_cmd.command == Word("cmd1")
    assert first_cmd.args == tuple()
    assert_descriptors(first_cmd)

    assert isinstance(first_cmd.pipe_command, Command)
    first_cmd_pipe_cmd1 = first_cmd.pipe_command
    assert first_cmd_pipe_cmd1.command == Word("cmd2")
    assert first_cmd_pipe_cmd1.args == tuple()
    assert_descriptors(first_cmd_pipe_cmd1)
    assert_single_cmd(first_cmd_pipe_cmd1)

    assert isinstance(first_cmd.next_command_operator, OperatorAnd)
    assert isinstance(first_cmd.next_command, Command)
    second_cmd = first_cmd.next_command
    assert second_cmd.command == Word("cmd3")
    assert second_cmd.args == tuple()
    assert_descriptors(second_cmd)

    assert isinstance(second_cmd.next_command_operator, OperatorOr)
    assert isinstance(second_cmd.next_command, Command)
    third_cmd = second_cmd.next_command
    assert third_cmd.command == Word("cmd4")
    assert third_cmd.args == tuple()
    assert_descriptors(third_cmd)
    assert_single_cmd(third_cmd)


def test_mixed_next_cmd_operators_with_pipes2(parser: Parser, formatter: Formatter):
    first_cmd = parser.parse("cmd1 arg1 | cmd2 arg2 arg3 | cmd3 && cmd4; cmd5 || cmd6 arg4 | cmd7")
    assert first_cmd.command == Word("cmd1")
    assert first_cmd.args == (Word("arg1"),)
    assert_descriptors(first_cmd)

    assert isinstance(first_cmd.pipe_command, Command)
    first_cmd_pipe_cmd1 = first_cmd.pipe_command
    assert first_cmd_pipe_cmd1.command == Word("cmd2")
    assert first_cmd_pipe_cmd1.args == (Word("arg2"), Word("arg3"))
    assert_descriptors(first_cmd_pipe_cmd1)

    assert isinstance(first_cmd_pipe_cmd1.pipe_command, Command)
    first_cmd_pipe_cmd2 = first_cmd_pipe_cmd1.pipe_command
    assert first_cmd_pipe_cmd2.command == Word("cmd3")
    assert first_cmd_pipe_cmd2.args == tuple()
    assert_descriptors(first_cmd_pipe_cmd2)
    assert_single_cmd(first_cmd_pipe_cmd2)

    assert isinstance(first_cmd.next_command_operator, OperatorAnd)
    assert isinstance(first_cmd.next_command, Command)
    second_cmd = first_cmd.next_command
    assert second_cmd.command == Word("cmd4")
    assert second_cmd.args == tuple()
    assert_descriptors(second_cmd)

    assert second_cmd.next_command_operator is None
    assert isinstance(second_cmd.next_command, Command)
    third_cmd = second_cmd.next_command
    assert third_cmd.command == Word("cmd5")
    assert third_cmd.args == tuple()
    assert_descriptors(third_cmd)

    assert isinstance(third_cmd.next_command_operator, OperatorOr)
    assert isinstance(third_cmd.next_command, Command)
    fourth_cmd = third_cmd.next_command
    assert fourth_cmd.command == Word("cmd6")
    assert fourth_cmd.args == (Word("arg4"),)
    assert_descriptors(fourth_cmd)
    assert fourth_cmd.next_command is None
    assert fourth_cmd.next_command_operator is None

    assert isinstance(fourth_cmd.pipe_command, Command)
    fourth_cmd_pipe_cmd1 = fourth_cmd.pipe_command
    assert fourth_cmd_pipe_cmd1.command == Word("cmd7")
    assert fourth_cmd_pipe_cmd1.args == tuple()
    assert_descriptors(fourth_cmd_pipe_cmd1)
    assert_single_cmd(fourth_cmd_pipe_cmd1)


@pytest.mark.parametrize("line", (
    "cmd1 >;",
    "cmd1 > ;",
    "cmd1 > ; cmd2",
    "cmd1 > &",
    "cmd1 > & cmd2",
    "cmd1 > && cmd2",
    "cmd1 > | cmd2",
    "cmd1 > || cmd2",
    "cmd1 > >",
))
def test_empty_redirect_filename(parser: Parser, line: str):
    def check(_line: str):
        with pytest.raises(EmptyRedirectParserFailure, match=make_match("No redirect filename provided.")):
            parser.parse(_line)

    check(line)
    check(line.replace(">", ">>"))
    check(line.replace(">", "<"))


@pytest.mark.parametrize("line", (
    "cmd > >",
    "cmd > >>",
    "cmd > <",
    "cmd >> >",
    "cmd >> >>",
    "cmd >> <",
    "cmd < >",
    "cmd < >>",
    "cmd < <",
))
def test_empty_redirect_filename_special(parser: Parser, line: str):
    with pytest.raises(EmptyRedirectParserFailure, match=make_match("No redirect filename provided.")):
        parser.parse(line)


@pytest.mark.parametrize("line", (
    "cmd1 >",
    "cmd1 >>",
    "cmd1 |",
    "cmd1 >&",
))
def test_unexpected_statement_finish(parser: Parser, line: str):
    with pytest.raises(UnexpectedStatementFinishParserFailure):
        parser.parse(line)


@pytest.mark.parametrize("line,failure_pos", (
    ("cmd1 ; ;", 7),
    ("cmd1 arg1;;", 10),
    ("cmd1 &&", 7),
    ("cmd1 ||", 7),
    ("cmd1 'arg1 arg2' && &&", 20),
    ("cmd1 'arg1 arg2' \\&\\& &&", 24),
    ("cmd1 'arg1 || arg2' || ||", 23),
    ("cmd1 'arg1 || arg2' \\|\\| || ||", 28),
    ("&& cmd2", 0),
    ("|| cmd2", 0),
    ("; cmd2", 0),
    ("&\\& cmd2", 0),
    ("|\\| cmd2", 0),
))
def test_empty_statement(parser: Parser, line: str, failure_pos: int):
    with pytest.raises(EmptyStatementParserFailure) as excinfo:
        parser.parse(line)
    assert excinfo.value.pos == failure_pos


@pytest.mark.parametrize("line", (
    "cmd1 '",
    'cmd1 "',
    "cmd1 ''\"",
    'cmd1 ""\'',
    "cmd1 '\\''",
    'cmd1 "\\"',
))
def test_unclosed_quotes(parser: Parser, line: str):
    with pytest.raises(UnclosedQuoteParserFailure):
        parser.parse(line)
