"""Content hashes always describe original bytes; shard hashes validate encoded bytes."""
import hashlib
import math
import zlib

from zfec import Decoder, Encoder


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(data, policy):
    payload = zlib.compress(data) if policy.get('compress') else data
    if policy['mode'] == 'replication':
        return [payload] * policy['n'], len(payload)
    k, total = policy['k'], policy['k'] + policy['m']
    width = max(1, math.ceil(len(payload) / k))
    padded = payload.ljust(width * k, b'\0')
    blocks = [padded[i * width:(i + 1) * width] for i in range(k)]
    return Encoder(k, total).encode(blocks), len(payload)


def decode(blocks, indexes, encoded_size, policy, original_hash):
    if policy['mode'] == 'replication':
        payload = blocks[0]
    else:
        k = policy['k']
        payload = b''.join(Decoder(k, k + policy['m']).decode(blocks[:k], indexes[:k]))[:encoded_size]
    original = zlib.decompress(payload) if policy.get('compress') else payload
    if sha(original) != original_hash:
        raise ValueError('Original chunk SHA-256 mismatch')
    return original
