# app\core\security.py

"""
Criptografia local usando DPAPI do Windows.

Escolha feita:
- usamos proteção vinculada à máquina (LOCAL_MACHINE)
- isso permite que o serviço Windows leia depois
- continua sendo MUITO melhor que texto puro

Observação:
isso não é segurança absoluta contra um administrador local,
mas resolve o problema grave de guardar tudo em JSON puro.
"""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes


CRYPTPROTECT_UI_FORBIDDEN = 0x01
CRYPTPROTECT_LOCAL_MACHINE = 0x04


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _bytes_to_blob(data: bytes) -> DATA_BLOB:
    """
    Converte bytes para DATA_BLOB, formato exigido pela API do Windows.
    """
    buffer = ctypes.create_string_buffer(data)
    blob = DATA_BLOB()
    blob.cbData = len(data)
    blob.pbData = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
    return blob


def _blob_to_bytes(blob: DATA_BLOB) -> bytes:
    """
    Converte DATA_BLOB de volta para bytes.
    """
    return ctypes.string_at(blob.pbData, blob.cbData)


def protect_text(plain_text: str) -> str:
    """
    Criptografa texto e retorna base64.
    """
    if plain_text is None:
        return ""

    data = plain_text.encode("utf-8")
    blob_in = _bytes_to_blob(data)
    blob_out = DATA_BLOB()

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    result = crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN | CRYPTPROTECT_LOCAL_MACHINE,
        ctypes.byref(blob_out),
    )

    if not result:
        raise RuntimeError("Falha ao criptografar dados com DPAPI.")

    try:
        encrypted_bytes = _blob_to_bytes(blob_out)
        return base64.b64encode(encrypted_bytes).decode("utf-8")
    finally:
        if blob_out.pbData:
            kernel32.LocalFree(blob_out.pbData)


def unprotect_text(encrypted_base64: str) -> str:
    """
    Descriptografa texto salvo em base64.
    """
    if not encrypted_base64:
        return ""

    encrypted_bytes = base64.b64decode(encrypted_base64.encode("utf-8"))
    blob_in = _bytes_to_blob(encrypted_bytes)
    blob_out = DATA_BLOB()

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    result = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    )

    if not result:
        raise RuntimeError("Falha ao descriptografar dados com DPAPI.")

    try:
        return _blob_to_bytes(blob_out).decode("utf-8")
    finally:
        if blob_out.pbData:
            kernel32.LocalFree(blob_out.pbData)
            