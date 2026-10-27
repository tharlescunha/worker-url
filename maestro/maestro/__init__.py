"""maestro: a biblioteca do robô para conversar com o orquestrador.

    from maestro import Maestro, CredencialInvalida
"""
from .core import CredencialInvalida, Maestro, MaestroError, TaskInfo, __version__
from .protocol import decode_line, encode_line

__all__ = ["Maestro", "CredencialInvalida", "MaestroError", "TaskInfo", "__version__", "decode_line", "encode_line"]
