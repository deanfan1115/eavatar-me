# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function, unicode_literals

from ..core import get_core_context, AvaError

_task_engine = None


def _get_task_engine():
    return get_core_context().lookup('taskengine')


def task(func):
    """
    Decorate a function as a task definition.

    :param func: the function to be called.
    :return: the task wrapping given function object.
    """
    return _get_task_engine().register(func)


def task_key(mod_name, func_name):
    """
    Generate a key uniquely identify the task defined by the module and function.

    :param mod_name: the module's full package name
    :param func_name: the function name
    :return: the key for identifying the task.
    """
    try:
        idx = mod_name.rindex('.')
    except ValueError:
        idx = -1

    if idx < 0:
        return mod_name + '.' + func_name
    else:
        return mod_name[idx+1:] + '.' + func_name


__all__ =[
    'task',
]
