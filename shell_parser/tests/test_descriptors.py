import pytest

import dataclasses
import re

from shell_parser.ast import CommandDescriptor, CommandFileDescriptor, InvalidDescriptorDataException
from shell_parser.ast import DescriptorRead, DescriptorWrite, RedirectionInput, RedirectionOutput, RedirectionAppend
from shell_parser.ast import File, DefaultFile, StdinTarget, StdoutTarget, StderrTarget


def test_missing_compulsory_data():
    with pytest.raises(TypeError, match=re.escape("__init__() missing 2 required positional arguments: 'mode' and 'descriptor'")):
        CommandDescriptor()

    with pytest.raises(TypeError, match=re.escape("__init__() missing 1 required positional argument: 'descriptor'")):
        CommandDescriptor(mode=DescriptorRead())


def test_invalid_descriptor_data():
    with pytest.raises(InvalidDescriptorDataException):
        file_target = File(name="test.txt")
        file_descriptor = CommandFileDescriptor(target=file_target, operator=RedirectionOutput())
        CommandDescriptor(mode=DescriptorRead(), descriptor=file_descriptor)

    with pytest.raises(InvalidDescriptorDataException):
        file_target = File(name="test.txt")
        file_descriptor = CommandFileDescriptor(target=file_target, operator=RedirectionAppend())
        CommandDescriptor(mode=DescriptorRead(), descriptor=file_descriptor)

    with pytest.raises(InvalidDescriptorDataException):
        file_target = File(name="test.txt")
        file_descriptor = CommandFileDescriptor(target=file_target, operator=RedirectionInput())
        CommandDescriptor(mode=DescriptorWrite(), descriptor=file_descriptor)

    with pytest.raises(InvalidDescriptorDataException):
        file_target = File(name="test.txt")
        file_descriptor = CommandFileDescriptor(target=file_target, operator=RedirectionInput())
        CommandDescriptor(mode=RedirectionInput(), descriptor=file_descriptor)


def test_immutability():
    file_target = File(name="test1.txt")
    default_file_target = DefaultFile(target=StdinTarget())
    file_descriptor = CommandFileDescriptor(target=file_target, operator=RedirectionOutput())
    descriptor = CommandDescriptor(mode=DescriptorWrite(), descriptor=file_descriptor)
    with pytest.raises(dataclasses.FrozenInstanceError):
        descriptor.mode = RedirectionOutput()
    with pytest.raises(dataclasses.FrozenInstanceError):
        descriptor.descriptor = CommandFileDescriptor(target=default_file_target, operator=RedirectionInput())
    with pytest.raises(dataclasses.FrozenInstanceError):
        descriptor.descriptor.target = default_file_target
    with pytest.raises(dataclasses.FrozenInstanceError):
        descriptor.descriptor.operator = RedirectionAppend()

    with pytest.raises(dataclasses.FrozenInstanceError):
        file_target.name = "test2.txt"
    with pytest.raises(dataclasses.FrozenInstanceError):
        default_file_target.target = StderrTarget()
