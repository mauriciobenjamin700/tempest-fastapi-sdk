"""Encryption at rest for the local object store.

The file format and the block cipher behind the ``encryption_keys`` of
:class:`~tempest_fastapi_sdk.storage.LocalObjectStorageClient`. An object is
cut into blocks of :data:`LOCAL_ENCRYPTION_BLOCK_SIZE` plaintext bytes, and
each block is sealed on its own with AES-256-GCM, so a write streams to disk
and a read — a whole download or one ``Range`` — decrypts only the blocks it
needs. This is the STREAM construction (Hoang, Reyhanitabar, Rogaway and
Vizár, "Online Authenticated-Encryption and its Nonce-Reuse Misuse-Resistance",
CRYPTO 2015), with the nonce layout Tink's ``AesGcmHkdfStreaming`` uses:

* **nonce** = a random 7-byte prefix chosen per file, a 4-byte big-endian
  block counter, and one byte that is ``1`` on the last block and ``0`` on
  every other. Moving a block changes its counter, and dropping the tail
  leaves a last block sealed as "not last", so reordering and truncation
  fail authentication.
* **associated data** = the whole header (magic, format version, key id,
  nonce prefix) followed by ``<bucket>/<key>``. A file copied or renamed to
  another key does not decrypt there.

Layout of an encrypted file::

    magic (8) | version (1) | key id length (1) | key id | nonce prefix (7)
    block 0 ciphertext | tag (16)
    block 1 ciphertext | tag (16)
    ...
    last block ciphertext | tag (16)

Every block but the last holds exactly :data:`LOCAL_ENCRYPTION_BLOCK_SIZE`
plaintext bytes; the last holds 1 to that many, or 0 for an empty object. The
plaintext size is therefore a function of the file size, and ``stat`` reads it
without decrypting anything.

The cipher is ``cryptography``'s
:class:`~cryptography.hazmat.primitives.ciphers.aead.AESGCM`, imported on first
use (the ``[storage-encryption]`` extra). Nothing here is a
new primitive: the module only fixes how the nonces and the associated data
are built.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING

from tempest_fastapi_sdk.exceptions.storage import StorageIntegrityException

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

LOCAL_ENCRYPTION_MAGIC: bytes = b"TFSDKENC"
"""First bytes of every encrypted object; a file without them is plaintext."""

LOCAL_ENCRYPTION_FORMAT_VERSION: int = 1
"""Version byte written after the magic; another value is refused."""

LOCAL_ENCRYPTION_BLOCK_SIZE: int = 64 * 1024
"""Plaintext bytes sealed per block (64 KiB).

Changing it makes every stored object unreadable: it is part of the format,
not a tuning knob.
"""

LOCAL_ENCRYPTION_KEY_BYTES: int = 32
"""Length of every key in ``encryption_keys``: AES-256."""

LOCAL_ENCRYPTION_NONCE_PREFIX_BYTES: int = 7
"""Random bytes per file at the start of every block nonce."""

LOCAL_ENCRYPTION_TAG_BYTES: int = 16
"""GCM tag appended to every block."""

LOCAL_ENCRYPTION_MAX_KEY_ID_BYTES: int = 255
"""Longest key id, in UTF-8 bytes: its length is stored in one byte."""

_MAX_BLOCKS: int = 2**32
"""Blocks a file can hold: the counter is four bytes (256 TiB of plaintext)."""

_FIXED_HEADER_BYTES: int = len(LOCAL_ENCRYPTION_MAGIC) + 2
"""Magic, version byte and key-id length byte."""


def check_encryption_keys(keys: Mapping[str, bytes]) -> dict[str, bytes]:
    """Validate a key ring and copy it, preserving its order.

    Args:
        keys (Mapping[str, bytes]): Key id to raw key. The first entry is
            the one new objects are written with.

    Returns:
        dict[str, bytes]: A copy of ``keys``.

    Raises:
        ValueError: When a key id is empty or longer than
            :data:`LOCAL_ENCRYPTION_MAX_KEY_ID_BYTES` UTF-8 bytes, or a key
            is not :data:`LOCAL_ENCRYPTION_KEY_BYTES` bytes. The message
            names the key id, never the key.
    """
    checked: dict[str, bytes] = {}
    for key_id, key in keys.items():
        encoded = key_id.encode("utf-8")
        if not encoded or len(encoded) > LOCAL_ENCRYPTION_MAX_KEY_ID_BYTES:
            raise ValueError(
                "encryption key ids must be 1 to "
                f"{LOCAL_ENCRYPTION_MAX_KEY_ID_BYTES} UTF-8 bytes"
            )
        if not isinstance(key, bytes) or len(key) != LOCAL_ENCRYPTION_KEY_BYTES:
            raise ValueError(
                f"encryption key {key_id!r} must be exactly "
                f"{LOCAL_ENCRYPTION_KEY_BYTES} bytes"
            )
        checked[key_id] = key
    return checked


@dataclass(frozen=True, slots=True)
class EncryptedHeader:
    """The header of one encrypted object.

    Attributes:
        key_id (str): Id of the key the blocks were sealed with.
        nonce_prefix (bytes): The file's random nonce prefix.
        raw (bytes): The header exactly as stored, part of every block's
            associated data.
    """

    key_id: str
    nonce_prefix: bytes
    raw: bytes

    @classmethod
    def build(cls, key_id: str, nonce_prefix: bytes) -> EncryptedHeader:
        """Serialize a header for a new object.

        Args:
            key_id (str): Id of the key the blocks are sealed with.
            nonce_prefix (bytes): The random prefix, of
                :data:`LOCAL_ENCRYPTION_NONCE_PREFIX_BYTES` bytes.

        Returns:
            EncryptedHeader: The header, with ``raw`` filled.
        """
        encoded = key_id.encode("utf-8")
        raw = (
            LOCAL_ENCRYPTION_MAGIC
            + bytes([LOCAL_ENCRYPTION_FORMAT_VERSION, len(encoded)])
            + encoded
            + nonce_prefix
        )
        return cls(key_id=key_id, nonce_prefix=nonce_prefix, raw=raw)

    @classmethod
    def read(cls, handle: IO[bytes]) -> EncryptedHeader | None:
        """Read the header at the start of an open file.

        Args:
            handle (IO[bytes]): The file, positioned at its first byte.

        Returns:
            EncryptedHeader | None: The header, with the handle left just
            after it; ``None`` when the file does not start with
            :data:`LOCAL_ENCRYPTION_MAGIC` (a plaintext object), with the
            handle back at its first byte.

        Raises:
            StorageIntegrityException: ``"format"`` when the magic is there
                but the version is unknown or the header is cut short.
        """
        fixed = handle.read(_FIXED_HEADER_BYTES)
        if not fixed.startswith(LOCAL_ENCRYPTION_MAGIC):
            handle.seek(0)
            return None
        if len(fixed) < _FIXED_HEADER_BYTES:
            raise StorageIntegrityException("format")
        version, id_length = fixed[-2], fixed[-1]
        if version != LOCAL_ENCRYPTION_FORMAT_VERSION or id_length == 0:
            raise StorageIntegrityException("format")
        rest = handle.read(id_length + LOCAL_ENCRYPTION_NONCE_PREFIX_BYTES)
        if len(rest) != id_length + LOCAL_ENCRYPTION_NONCE_PREFIX_BYTES:
            raise StorageIntegrityException("format")
        try:
            key_id = rest[:id_length].decode("utf-8")
        except UnicodeDecodeError:
            raise StorageIntegrityException("format") from None
        return cls(key_id=key_id, nonce_prefix=rest[id_length:], raw=fixed + rest)


def block_layout(ciphertext_bytes: int) -> tuple[int, int]:
    """Count the blocks of an encrypted body and the plaintext they hold.

    Args:
        ciphertext_bytes (int): Size of the file after the header.

    Returns:
        tuple[int, int]: The number of blocks and the plaintext size.

    Raises:
        StorageIntegrityException: ``"format"`` when no writer of this
            format produces a body of that size — a file cut inside a tag,
            or with no block at all.
    """
    sealed = LOCAL_ENCRYPTION_BLOCK_SIZE + LOCAL_ENCRYPTION_TAG_BYTES
    full, rest = divmod(ciphertext_bytes, sealed)
    if rest == 0 and full > 0:
        return full, full * LOCAL_ENCRYPTION_BLOCK_SIZE
    if rest > LOCAL_ENCRYPTION_TAG_BYTES or (
        rest == LOCAL_ENCRYPTION_TAG_BYTES and full == 0
    ):
        return full + 1, full * LOCAL_ENCRYPTION_BLOCK_SIZE + rest - (
            LOCAL_ENCRYPTION_TAG_BYTES
        )
    raise StorageIntegrityException("format")


def _nonce(prefix: bytes, index: int, *, last: bool) -> bytes:
    """Build the nonce of one block.

    Args:
        prefix (bytes): The file's random prefix.
        index (int): The block's position, from 0.
        last (bool): Whether this is the file's last block.

    Returns:
        bytes: The 12-byte GCM nonce.

    Raises:
        ValueError: When ``index`` does not fit the 4-byte counter.
    """
    if index >= _MAX_BLOCKS:
        raise ValueError("object too large for the encrypted format")
    return prefix + index.to_bytes(4, "big") + (b"\x01" if last else b"\x00")


class LocalObjectCipher:
    """The key ring of an encrypted local store.

    The first key (insertion order) seals new objects; any key opens the
    objects sealed with it, by the id in their header — which is how a key
    is rotated: put the new one first, keep the old one until
    :meth:`~tempest_fastapi_sdk.storage.LocalObjectStorageClient.reencrypt`
    has rewritten everything.

    Attributes:
        write_key_id (str): Id of the key new objects are sealed with.
    """

    def __init__(self, keys: Mapping[str, bytes]) -> None:
        """Validate the keys and build one AES-GCM instance per key.

        Args:
            keys (Mapping[str, bytes]): Key id to 32-byte key; at least one.

        Raises:
            ValueError: When ``keys`` is empty or a key fails
                :func:`check_encryption_keys`.
            ImportError: When ``cryptography`` is not installed.
        """
        checked = check_encryption_keys(keys)
        if not checked:
            raise ValueError("encryption_keys needs at least one key")
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as exc:
            raise ImportError(
                "Encrypting the local store needs cryptography. "
                "Install with: pip install tempest-fastapi-sdk[storage-encryption]"
            ) from exc
        self._ciphers: dict[str, AESGCM] = {
            key_id: AESGCM(key) for key_id, key in checked.items()
        }
        self.write_key_id: str = next(iter(checked))

    def has_key(self, key_id: str) -> bool:
        """Tell whether the ring holds a key.

        Args:
            key_id (str): The key id.

        Returns:
            bool: ``True`` when objects sealed with it can be opened.
        """
        return key_id in self._ciphers

    def encryptor(self, object_name: str) -> BlockEncryptor:
        """Start sealing a new object with the write key.

        Args:
            object_name (str): ``<bucket>/<key>``, bound into every block.

        Returns:
            BlockEncryptor: The encryptor, with a fresh nonce prefix.
        """
        header = EncryptedHeader.build(
            self.write_key_id, os.urandom(LOCAL_ENCRYPTION_NONCE_PREFIX_BYTES)
        )
        return BlockEncryptor(
            self._ciphers[self.write_key_id],
            header,
            header.raw + object_name.encode("utf-8"),
        )

    def reader(
        self,
        handle: IO[bytes],
        header: EncryptedHeader,
        *,
        file_size: int,
        object_name: str,
    ) -> EncryptedObjectReader:
        """Open the blocks of a stored object for reading.

        Args:
            handle (IO[bytes]): The open file.
            header (EncryptedHeader): Its header, already read.
            file_size (int): The file's size on disk.
            object_name (str): ``<bucket>/<key>`` the object is read as.

        Returns:
            EncryptedObjectReader: The reader.

        Raises:
            StorageIntegrityException: ``"unknown_key"`` when the ring lacks
                the header's key id, ``"format"`` when the size is not one
                this format produces.
        """
        cipher = self._ciphers.get(header.key_id)
        if cipher is None:
            raise StorageIntegrityException("unknown_key")
        return EncryptedObjectReader(
            handle,
            cipher,
            header,
            ciphertext_bytes=file_size - len(header.raw),
            associated_data=header.raw + object_name.encode("utf-8"),
        )


class BlockEncryptor:
    """Seal a plaintext stream block by block, holding back the last block.

    A full block is only sealed once more data arrives, because the last
    block is sealed differently and the encryptor cannot know a block is
    the last before :meth:`finalize`.

    Attributes:
        header (EncryptedHeader): The header to write before the blocks.
    """

    def __init__(
        self,
        cipher: AESGCM,
        header: EncryptedHeader,
        associated_data: bytes,
    ) -> None:
        """Keep the cipher and the per-file values.

        Args:
            cipher (AESGCM): The write key's cipher.
            header (EncryptedHeader): The new object's header.
            associated_data (bytes): Header plus object name.
        """
        self.header: EncryptedHeader = header
        self._cipher: AESGCM = cipher
        self._associated_data: bytes = associated_data
        self._pending: bytearray = bytearray()
        self._index: int = 0

    def _seal(self, block: bytes, *, last: bool) -> bytes:
        """Seal one block and advance the counter.

        Args:
            block (bytes): The plaintext block.
            last (bool): Whether it is the object's last block.

        Returns:
            bytes: Ciphertext followed by the tag.
        """
        sealed = self._cipher.encrypt(
            _nonce(self.header.nonce_prefix, self._index, last=last),
            block,
            self._associated_data,
        )
        self._index += 1
        return sealed

    def update(self, data: bytes) -> bytes:
        """Take more plaintext and return the blocks it completed.

        Args:
            data (bytes): The next plaintext bytes.

        Returns:
            bytes: Sealed blocks, possibly empty.
        """
        self._pending += data
        out = bytearray()
        while len(self._pending) > LOCAL_ENCRYPTION_BLOCK_SIZE:
            out += self._seal(
                bytes(self._pending[:LOCAL_ENCRYPTION_BLOCK_SIZE]), last=False
            )
            del self._pending[:LOCAL_ENCRYPTION_BLOCK_SIZE]
        return bytes(out)

    def finalize(self) -> bytes:
        """Seal what is left as the last block.

        Returns:
            bytes: The last sealed block — the only block of an empty
            object, sealing zero bytes.
        """
        block = bytes(self._pending)
        self._pending.clear()
        return self._seal(block, last=True)


class EncryptedObjectReader:
    """Decrypt any byte range of a stored object, one authenticated block at a time.

    Attributes:
        block_count (int): Blocks in the file.
        plaintext_size (int): Bytes the object holds once decrypted.
    """

    def __init__(
        self,
        handle: IO[bytes],
        cipher: AESGCM,
        header: EncryptedHeader,
        *,
        ciphertext_bytes: int,
        associated_data: bytes,
    ) -> None:
        """Keep the open file and compute its layout.

        Args:
            handle (IO[bytes]): The open file.
            cipher (AESGCM): The cipher of the header's key.
            header (EncryptedHeader): The file's header.
            ciphertext_bytes (int): Size of the file after the header.
            associated_data (bytes): Header plus object name.

        Raises:
            StorageIntegrityException: ``"format"`` when the size is not one
                this format produces.
        """
        self.block_count, self.plaintext_size = block_layout(ciphertext_bytes)
        self._handle: IO[bytes] = handle
        self._cipher: AESGCM = cipher
        self._header: EncryptedHeader = header
        self._associated_data: bytes = associated_data

    def read_block(self, index: int) -> bytes:
        """Read and authenticate one block.

        The block counts as the last one when it is the last in the file,
        so a file cut at a block boundary fails here: its new last block
        was sealed as "not last".

        Args:
            index (int): The block, from 0.

        Returns:
            bytes: Its plaintext, only after the tag verified.

        Raises:
            StorageIntegrityException: ``"authentication"`` when the tag
                does not verify, ``"format"`` when the block is cut short.
        """
        from cryptography.exceptions import InvalidTag

        sealed_size = LOCAL_ENCRYPTION_BLOCK_SIZE + LOCAL_ENCRYPTION_TAG_BYTES
        self._handle.seek(len(self._header.raw) + index * sealed_size)
        sealed = self._handle.read(sealed_size)
        last = index == self.block_count - 1
        if len(sealed) < LOCAL_ENCRYPTION_TAG_BYTES or (
            not last and len(sealed) != sealed_size
        ):
            raise StorageIntegrityException("format")
        try:
            return self._cipher.decrypt(
                _nonce(self._header.nonce_prefix, index, last=last),
                sealed,
                self._associated_data,
            )
        except InvalidTag:
            raise StorageIntegrityException("authentication") from None

    def iter_range(self, offset: int, length: int | None) -> Iterator[bytes]:
        """Yield the plaintext of ``[offset, offset + length)``, block by block.

        Only the blocks that overlap the range are read and decrypted. An
        empty range past the end still authenticates the last block, so an
        empty or tampered object is never served as "nothing".

        Args:
            offset (int): First plaintext byte.
            length (int | None): Bytes to yield; ``None`` reads to the end.
                A range past the end yields what exists.

        Yields:
            bytes: The plaintext of each overlapping block, trimmed to the
            range, in order. Each one is yielded only after its block
            authenticated.

        Raises:
            StorageIntegrityException: When a block fails
                :meth:`read_block`.
        """
        end = self.plaintext_size if length is None else offset + length
        end = min(end, self.plaintext_size)
        if offset >= end:
            self.read_block(self.block_count - 1)
            return
        first = offset // LOCAL_ENCRYPTION_BLOCK_SIZE
        last = (end - 1) // LOCAL_ENCRYPTION_BLOCK_SIZE
        for index in range(first, last + 1):
            block = self.read_block(index)
            start = index * LOCAL_ENCRYPTION_BLOCK_SIZE
            yield block[max(offset - start, 0) : end - start]


__all__: list[str] = [
    "LOCAL_ENCRYPTION_BLOCK_SIZE",
    "LOCAL_ENCRYPTION_FORMAT_VERSION",
    "LOCAL_ENCRYPTION_KEY_BYTES",
    "LOCAL_ENCRYPTION_MAGIC",
    "LOCAL_ENCRYPTION_MAX_KEY_ID_BYTES",
    "LOCAL_ENCRYPTION_NONCE_PREFIX_BYTES",
    "LOCAL_ENCRYPTION_TAG_BYTES",
    "BlockEncryptor",
    "EncryptedHeader",
    "EncryptedObjectReader",
    "LocalObjectCipher",
    "block_layout",
    "check_encryption_keys",
]
