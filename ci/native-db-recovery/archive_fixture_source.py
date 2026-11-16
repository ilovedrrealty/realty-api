import argparse, getpass, hashlib, io, json, os, pathlib, secrets, zipfile
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MAGIC = b'ILDR-BACKUP-V1\n'


def seal(data, public):
    key, nonce = AESGCM.generate_key(bit_length=256), secrets.token_bytes(12)
    wrapped = public.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    header = MAGIC + len(wrapped).to_bytes(4, 'big') + wrapped + nonce
    return header + AESGCM(key).encrypt(nonce, data, header)


def unseal(blob, private):
    if not blob.startswith(MAGIC): raise ValueError('Unsupported archive format')
    pos = len(MAGIC)
    size = int.from_bytes(blob[pos:pos+4], 'big')
    if size != private.key_size // 8: raise ValueError('Invalid wrapped key size')
    pos += 4
    key = private.decrypt(blob[pos:pos+size], padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    pos += size
    nonce = blob[pos:pos+12]
    pos += 12
    return AESGCM(key).decrypt(nonce, blob[pos:], blob[:pos])
