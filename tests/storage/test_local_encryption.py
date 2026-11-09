"""Encryption at rest of ``LocalObjectStorageClient`` (``encryption_keys``).

What is pinned: no plaintext reaches the disk on any write path, every read
path returns the original bytes, ``stat`` reports the plaintext size, and a
file that was changed, cut, reordered or moved to another key raises
``StorageIntegrityException`` instead of serving bytes. Rotation, the
plaintext gate and the migration are pinned beside them.
"""

from __future__ import annotations

import hashlib
import io
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from tempest_fastapi_sdk import StorageIntegrityException
from tempest_fastapi_sdk.privacy import SubjectObjectStorage
from tempest_fastapi_sdk.storage import (
    LOCAL_ENCRYPTION_BLOCK_SIZE,
    LOCAL_ENCRYPTION_MAGIC,
    LocalObjectStorageClient,
)
from tempest_fastapi_sdk.storage.local_crypto import (
    LOCAL_ENCRYPTION_TAG_BYTES,
    EncryptedHeader,
    EncryptedObjectReader,
)

SECRET: str = "test-local-encryption-secret"
"""Signing secret of every client built here."""

KEY_1: bytes = bytes(range(32))
"""First test key."""

KEY_2: bytes = bytes(range(32, 64))
"""Second test key, for rotation."""

BLOCK: int = LOCAL_ENCRYPTION_BLOCK_SIZE
"""Plaintext bytes per block."""

SEALED: int = BLOCK + LOCAL_ENCRYPTION_TAG_BYTES
"""Bytes one full block takes on disk."""

SIZES: tuple[int, ...] = (0, 1, BLOCK - 1, BLOCK, BLOCK + 1, 3 * BLOCK, 3 * BLOCK + 7)
"""Payload sizes around the block boundaries, the empty object included."""


def make_client(base: Path, **overrides: object) -> LocalObjectStorageClient:
    """Build an encrypting client rooted at ``base``.

    Args:
        base (Path): The store's root.
        **overrides (object): Keyword arguments replacing the defaults.

    Returns:
        LocalObjectStorageClient: The client.
    """
    kwargs: dict[str, object] = {
        "public_url": "https://api.example.com",
        "signing_secret": SECRET,
        "default_bucket": "media",
        "encryption_keys": {"k1": KEY_1},
    }
    kwargs.update(overrides)
    return LocalObjectStorageClient(base, **kwargs)  # type: ignore[arg-type]


def payload(size: int) -> bytes:
    """Build a recognizable payload of ``size`` bytes.

    Args:
        size (int): Length.

    Returns:
        bytes: A repeating ASCII pattern, easy to spot in a raw file.
    """
    pattern = b"PLAINTEXT-MARKER-0123456789"
    return (pattern * (size // len(pattern) + 1))[:size]


async def collect(stream: AsyncIterator[bytes]) -> bytes:
    """Drain a chunk stream.

    Args:
        stream (AsyncIterator[bytes]): The stream.

    Returns:
        bytes: Every chunk joined.
    """
    return b"".join([chunk async for chunk in stream])


async def chunked(data: bytes, size: int) -> AsyncIterator[bytes]:
    """Yield ``data`` in chunks of ``size`` bytes.

    Args:
        data (bytes): The payload.
        size (int): Chunk size.

    Yields:
        bytes: The next chunk.
    """
    for start in range(0, len(data), size):
        yield data[start : start + size]


def raw(client: LocalObjectStorageClient, key: str) -> bytes:
    """Read an object's file exactly as stored.

    Args:
        client (LocalObjectStorageClient): The store.
        key (str): Object key.

    Returns:
        bytes: The file's bytes.
    """
    return client.object_path(key).read_bytes()


def header_length(client: LocalObjectStorageClient, key: str) -> int:
    """Measure the header of a stored encrypted object.

    Args:
        client (LocalObjectStorageClient): The store.
        key (str): Object key.

    Returns:
        int: Header bytes.
    """
    with client.object_path(key).open("rb") as handle:
        header = EncryptedHeader.read(handle)
    assert header is not None
    return len(header.raw)


class TestAtRest:
    async def test_put_object_bytes_leaves_no_plaintext_on_disk(
        self, tmp_path: Path
    ) -> None:
        """Bytes written by ``put_object`` are sealed on disk."""
        client = make_client(tmp_path)
        data = payload(3 * BLOCK + 7)
        await client.put_object("a.bin", data)
        stored = raw(client, "a.bin")
        assert stored.startswith(LOCAL_ENCRYPTION_MAGIC)
        assert b"PLAINTEXT-MARKER" not in stored

    async def test_put_object_stream_leaves_no_plaintext_on_disk(
        self, tmp_path: Path
    ) -> None:
        """A stream given to ``put_object`` is sealed too, up to ``length``."""
        client = make_client(tmp_path)
        data = payload(BLOCK + 10)
        await client.put_object("a.bin", io.BytesIO(data), length=BLOCK + 5)
        assert b"PLAINTEXT-MARKER" not in raw(client, "a.bin")
        assert await collect(await client.stream_object("a.bin")) == data[: BLOCK + 5]

    async def test_write_stream_leaves_no_plaintext_on_disk(
        self, tmp_path: Path
    ) -> None:
        """``write_stream`` seals as it writes."""
        client = make_client(tmp_path)
        data = payload(2 * BLOCK + 3)
        await client.write_stream("a.bin", chunked(data, 1000))
        assert b"PLAINTEXT-MARKER" not in raw(client, "a.bin")

    async def test_etag_is_the_md5_of_the_stored_file(self, tmp_path: Path) -> None:
        """The metadata never holds a hash of the plaintext."""
        client = make_client(tmp_path)
        data = payload(100)
        etag = await client.put_object("a.bin", data)
        assert etag == hashlib.md5(raw(client, "a.bin")).hexdigest()
        assert etag != hashlib.md5(data).hexdigest()
        assert (await client.stat_object("a.bin")).etag == etag

    async def test_same_payload_twice_seals_differently(self, tmp_path: Path) -> None:
        """Every file draws its own nonce prefix."""
        client = make_client(tmp_path)
        await client.put_object("a.bin", b"same")
        first = raw(client, "a.bin")
        await client.put_object("a.bin", b"same")
        assert raw(client, "a.bin") != first


class TestRoundTrip:
    @pytest.mark.parametrize("size", SIZES)
    async def test_put_object_then_stream(self, tmp_path: Path, size: int) -> None:
        """Every size around a block boundary reads back exactly."""
        client = make_client(tmp_path)
        data = payload(size)
        await client.put_object("a.bin", data, content_type="text/plain")
        assert await collect(await client.stream_object("a.bin")) == data

    @pytest.mark.parametrize("size", SIZES)
    async def test_write_stream_then_stream(self, tmp_path: Path, size: int) -> None:
        """Chunks that straddle blocks still seal and open exactly."""
        client = make_client(tmp_path)
        data = payload(size)
        await client.write_stream("a.bin", chunked(data, 4099))
        assert await collect(await client.stream_object("a.bin")) == data

    @pytest.mark.parametrize("size", SIZES)
    async def test_stat_reports_the_plaintext_size(
        self, tmp_path: Path, size: int
    ) -> None:
        """``stat_object`` answers the payload size, not the file size."""
        client = make_client(tmp_path)
        await client.put_object("a.bin", payload(size), content_type="text/plain")
        stat = await client.stat_object("a.bin")
        assert stat.size == size
        assert stat.content_type == "text/plain"
        assert client.object_path("a.bin").stat().st_size > size

    async def test_disk_size_is_header_plus_tag_per_block(self, tmp_path: Path) -> None:
        """A multiple of the block ends on a full last block, no empty one."""
        client = make_client(tmp_path)
        await client.put_object("a.bin", payload(3 * BLOCK))
        on_disk = client.object_path("a.bin").stat().st_size
        assert on_disk == header_length(client, "a.bin") + 3 * SEALED

    @pytest.mark.parametrize(
        ("offset", "length"),
        [
            (0, 1),
            (BLOCK - 1, 2),
            (BLOCK, BLOCK),
            (10, 2 * BLOCK + 5),
            (3 * BLOCK, 7),
            (3 * BLOCK + 6, None),
            (5, None),
            (3 * BLOCK + 7, None),
            (2 * BLOCK, 10 * BLOCK),
        ],
    )
    async def test_ranges_decrypt_exactly(
        self, tmp_path: Path, offset: int, length: int | None
    ) -> None:
        """A range returns the same bytes the plaintext slice would."""
        client = make_client(tmp_path)
        data = payload(3 * BLOCK + 7)
        await client.put_object("a.bin", data)
        stream = await client.stream_object(
            "a.bin", offset=offset, length=length, chunk_size=1000
        )
        end = None if length is None else offset + length
        assert await collect(stream) == data[offset:end]

    async def test_chunk_size_is_honoured(self, tmp_path: Path) -> None:
        """Decrypted blocks are cut to ``chunk_size``."""
        client = make_client(tmp_path)
        await client.put_object("a.bin", payload(BLOCK + 10))
        chunks = [c async for c in await client.stream_object("a.bin", chunk_size=4096)]
        assert all(len(c) <= 4096 for c in chunks)
        assert b"".join(chunks) == payload(BLOCK + 10)

    async def test_capped_read_decrypts_only_the_blocks_it_needs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``SubjectObjectStorage.read(max_bytes=)`` stops at the first block."""
        client = make_client(tmp_path)
        storage = SubjectObjectStorage(client, prefix="users")
        await storage.put(1, "big.bin", payload(3 * BLOCK + 7))
        decrypted: list[int] = []
        original = EncryptedObjectReader.read_block

        def counting(reader: EncryptedObjectReader, index: int) -> bytes:
            decrypted.append(index)
            return original(reader, index)

        monkeypatch.setattr(EncryptedObjectReader, "read_block", counting)
        assert await storage.read(1, "big.bin", max_bytes=10) == payload(10)
        assert decrypted == [0]


class TestIntegrity:
    async def stored(self, tmp_path: Path) -> tuple[LocalObjectStorageClient, int]:
        """Store a three-and-a-bit block object.

        Args:
            tmp_path (Path): The store's root.

        Returns:
            tuple[LocalObjectStorageClient, int]: The client and the header
            length of ``a.bin``.
        """
        client = make_client(tmp_path)
        await client.put_object("a.bin", payload(3 * BLOCK + 7))
        return client, header_length(client, "a.bin")

    async def test_flipped_byte_in_a_later_block_fails_while_streaming(
        self, tmp_path: Path
    ) -> None:
        """Only authenticated blocks come out; the bad one raises."""
        client, head = await self.stored(tmp_path)
        path = client.object_path("a.bin")
        data = bytearray(path.read_bytes())
        data[head + SEALED + 5] ^= 0x01
        path.write_bytes(bytes(data))
        stream = await client.stream_object("a.bin", chunk_size=BLOCK)
        served = [await anext(stream)]
        with pytest.raises(StorageIntegrityException) as caught:
            served.extend([chunk async for chunk in stream])
        assert caught.value.reason == "authentication"
        assert served == [payload(BLOCK)]

    async def test_flipped_byte_in_the_first_block_fails_on_open(
        self, tmp_path: Path
    ) -> None:
        """The first block is checked before the stream is handed out."""
        client, head = await self.stored(tmp_path)
        path = client.object_path("a.bin")
        data = bytearray(path.read_bytes())
        data[head + 3] ^= 0x80
        path.write_bytes(bytes(data))
        with pytest.raises(StorageIntegrityException) as caught:
            await client.stream_object("a.bin")
        assert caught.value.reason == "authentication"

    async def test_flipped_tag_fails(self, tmp_path: Path) -> None:
        """A tag byte is as protected as a ciphertext byte."""
        client, head = await self.stored(tmp_path)
        path = client.object_path("a.bin")
        data = bytearray(path.read_bytes())
        data[head + BLOCK + 1] ^= 0x01
        path.write_bytes(bytes(data))
        with pytest.raises(StorageIntegrityException):
            await client.stream_object("a.bin")

    async def test_changed_nonce_prefix_fails(self, tmp_path: Path) -> None:
        """The header is part of every block's associated data."""
        client, head = await self.stored(tmp_path)
        path = client.object_path("a.bin")
        data = bytearray(path.read_bytes())
        data[head - 1] ^= 0x01
        path.write_bytes(bytes(data))
        with pytest.raises(StorageIntegrityException) as caught:
            await client.stream_object("a.bin")
        assert caught.value.reason == "authentication"

    async def test_unknown_version_fails_as_format(self, tmp_path: Path) -> None:
        """A version byte this code does not know is refused."""
        client, _ = await self.stored(tmp_path)
        path = client.object_path("a.bin")
        data = bytearray(path.read_bytes())
        data[len(LOCAL_ENCRYPTION_MAGIC)] = 99
        path.write_bytes(bytes(data))
        with pytest.raises(StorageIntegrityException) as caught:
            await client.stream_object("a.bin")
        assert caught.value.reason == "format"

    async def test_truncated_at_a_block_boundary_fails(self, tmp_path: Path) -> None:
        """Dropping the last block leaves a last block sealed as "not last"."""
        client, head = await self.stored(tmp_path)
        path = client.object_path("a.bin")
        path.write_bytes(path.read_bytes()[: head + 3 * SEALED])
        assert (await client.stat_object("a.bin")).size == 3 * BLOCK
        stream = await client.stream_object("a.bin")
        with pytest.raises(StorageIntegrityException) as caught:
            await collect(stream)
        assert caught.value.reason == "authentication"

    async def test_truncated_at_a_block_boundary_fails_on_a_range_of_the_tail(
        self, tmp_path: Path
    ) -> None:
        """A range of the new last block fails as well."""
        client, head = await self.stored(tmp_path)
        path = client.object_path("a.bin")
        path.write_bytes(path.read_bytes()[: head + 2 * SEALED])
        with pytest.raises(StorageIntegrityException):
            await client.stream_object("a.bin", offset=BLOCK + 3, length=2)

    async def test_truncated_inside_a_tag_fails_as_format(self, tmp_path: Path) -> None:
        """A file cut so the last block cannot hold a tag is malformed."""
        client, head = await self.stored(tmp_path)
        path = client.object_path("a.bin")
        path.write_bytes(path.read_bytes()[: head + 3 * SEALED + 5])
        with pytest.raises(StorageIntegrityException) as caught:
            await client.stat_object("a.bin")
        assert caught.value.reason == "format"
        with pytest.raises(StorageIntegrityException):
            await client.stream_object("a.bin")

    async def test_truncated_mid_block_fails(self, tmp_path: Path) -> None:
        """Cutting into the last block breaks its tag."""
        client, _ = await self.stored(tmp_path)
        path = client.object_path("a.bin")
        path.write_bytes(path.read_bytes()[:-3])
        stream = await client.stream_object("a.bin")
        with pytest.raises(StorageIntegrityException):
            await collect(stream)

    async def test_header_cut_short_fails_as_format(self, tmp_path: Path) -> None:
        """A file that holds the magic and little else is malformed."""
        client, _ = await self.stored(tmp_path)
        path = client.object_path("a.bin")
        path.write_bytes(path.read_bytes()[: len(LOCAL_ENCRYPTION_MAGIC) + 3])
        with pytest.raises(StorageIntegrityException) as caught:
            await client.stream_object("a.bin")
        assert caught.value.reason == "format"

    async def test_swapped_blocks_fail(self, tmp_path: Path) -> None:
        """Each block's counter is in its nonce, so order is authenticated."""
        client, head = await self.stored(tmp_path)
        path = client.object_path("a.bin")
        data = path.read_bytes()
        first, second = (
            data[head : head + SEALED],
            data[head + SEALED : head + 2 * SEALED],
        )
        path.write_bytes(data[:head] + second + first + data[head + 2 * SEALED :])
        with pytest.raises(StorageIntegrityException) as caught:
            await client.stream_object("a.bin")
        assert caught.value.reason == "authentication"

    async def test_file_moved_to_another_key_does_not_decrypt(
        self, tmp_path: Path
    ) -> None:
        """The object's ``bucket/key`` is bound into every block."""
        client, _ = await self.stored(tmp_path)
        target = client.object_path("b.bin")
        os.replace(client.object_path("a.bin"), target)
        assert (await client.stat_object("b.bin")).size == 3 * BLOCK + 7
        with pytest.raises(StorageIntegrityException) as caught:
            await client.stream_object("b.bin")
        assert caught.value.reason == "authentication"

    async def test_file_moved_to_another_bucket_does_not_decrypt(
        self, tmp_path: Path
    ) -> None:
        """The bucket is part of the bound name too."""
        client, _ = await self.stored(tmp_path)
        target = client.object_path("a.bin", bucket="other")
        target.parent.mkdir(parents=True)
        os.replace(client.object_path("a.bin"), target)
        with pytest.raises(StorageIntegrityException):
            await client.stream_object("a.bin", bucket="other")

    async def test_tampered_empty_object_fails(self, tmp_path: Path) -> None:
        """An empty object is still one authenticated block."""
        client = make_client(tmp_path)
        await client.put_object("e.bin", b"")
        path = client.object_path("e.bin")
        data = bytearray(path.read_bytes())
        data[-1] ^= 0x01
        path.write_bytes(bytes(data))
        with pytest.raises(StorageIntegrityException):
            await client.stream_object("e.bin")

    async def test_key_not_in_the_ring_fails(self, tmp_path: Path) -> None:
        """A file sealed with a key the client lacks is ``unknown_key``."""
        await make_client(tmp_path).put_object("a.bin", b"secret")
        other = make_client(tmp_path, encryption_keys={"k9": KEY_2})
        assert (await other.stat_object("a.bin")).size == 6
        with pytest.raises(StorageIntegrityException) as caught:
            await other.stream_object("a.bin")
        assert caught.value.reason == "unknown_key"

    async def test_same_id_with_another_key_fails(self, tmp_path: Path) -> None:
        """A wrong key under the right id does not authenticate."""
        await make_client(tmp_path).put_object("a.bin", b"secret")
        other = make_client(tmp_path, encryption_keys={"k1": KEY_2})
        with pytest.raises(StorageIntegrityException) as caught:
            await other.stream_object("a.bin")
        assert caught.value.reason == "authentication"


class TestRotation:
    async def test_old_objects_read_after_a_new_key_goes_first(
        self, tmp_path: Path
    ) -> None:
        """Writes switch to the first key; reads follow the header."""
        await make_client(tmp_path).put_object("old.bin", payload(BLOCK + 1))
        rotated = make_client(tmp_path, encryption_keys={"k2": KEY_2, "k1": KEY_1})
        assert rotated.write_key_id == "k2"
        assert await collect(await rotated.stream_object("old.bin")) == payload(
            BLOCK + 1
        )
        await rotated.put_object("new.bin", b"new")
        with rotated.object_path("new.bin").open("rb") as handle:
            header = EncryptedHeader.read(handle)
        assert header is not None and header.key_id == "k2"

    async def test_reencrypt_moves_objects_to_the_write_key(
        self, tmp_path: Path
    ) -> None:
        """After ``reencrypt`` the old key can leave the ring."""
        first = make_client(tmp_path)
        await first.put_object("a.bin", payload(2 * BLOCK), content_type="image/png")
        await first.put_object("b.bin", b"bee", metadata={"owner": "1"})
        rotated = make_client(tmp_path, encryption_keys={"k2": KEY_2, "k1": KEY_1})
        assert await rotated.reencrypt() == 2
        assert await rotated.reencrypt() == 0
        only_new = make_client(tmp_path, encryption_keys={"k2": KEY_2})
        assert await collect(await only_new.stream_object("a.bin")) == payload(
            2 * BLOCK
        )
        stat = await only_new.stat_object("b.bin")
        assert stat.metadata == {"owner": "1"}
        assert (await only_new.stat_object("a.bin")).content_type == "image/png"
        assert stat.etag == hashlib.md5(raw(only_new, "b.bin")).hexdigest()

    async def test_reencrypt_skips_plaintext(self, tmp_path: Path) -> None:
        """Plaintext is ``encrypt_existing``'s job."""
        plain = LocalObjectStorageClient(
            tmp_path, public_url="https://x.example", signing_secret=SECRET
        )
        await plain.put_object("p.bin", b"plain")
        assert await make_client(tmp_path).reencrypt() == 0
        assert raw(plain, "p.bin") == b"plain"

    async def test_reencrypt_stops_on_an_unreadable_object(
        self, tmp_path: Path
    ) -> None:
        """A file whose key left the ring fails loudly, not silently."""
        await make_client(tmp_path).put_object("a.bin", b"x")
        stranger = make_client(tmp_path, encryption_keys={"k3": KEY_2})
        with pytest.raises(StorageIntegrityException):
            await stranger.reencrypt()


class TestPlaintextAndMigration:
    async def plain_store(self, tmp_path: Path) -> LocalObjectStorageClient:
        """Write two plaintext objects with a client that has no keys.

        Args:
            tmp_path (Path): The store's root.

        Returns:
            LocalObjectStorageClient: The plaintext client.
        """
        plain = LocalObjectStorageClient(
            tmp_path,
            public_url="https://x.example",
            signing_secret=SECRET,
            default_bucket="media",
        )
        await plain.put_object("p.txt", payload(BLOCK + 3), content_type="text/plain")
        await plain.put_object("q.txt", b"short", bucket="docs")
        return plain

    async def test_plaintext_is_refused_by_default(self, tmp_path: Path) -> None:
        """Without the flag a file with no header is an error, not a read."""
        await self.plain_store(tmp_path)
        with pytest.raises(StorageIntegrityException) as caught:
            await make_client(tmp_path).stream_object("p.txt")
        assert caught.value.reason == "plaintext"

    async def test_plaintext_is_read_with_the_flag(self, tmp_path: Path) -> None:
        """``allow_plaintext_read`` serves it as it is, ranges included."""
        await self.plain_store(tmp_path)
        client = make_client(tmp_path, allow_plaintext_read=True)
        assert await collect(await client.stream_object("p.txt")) == payload(BLOCK + 3)
        part = await client.stream_object("p.txt", offset=5, length=10)
        assert await collect(part) == payload(BLOCK + 3)[5:15]
        assert (await client.stat_object("p.txt")).size == BLOCK + 3

    async def test_encrypt_existing_is_idempotent(self, tmp_path: Path) -> None:
        """The migration converts once and skips what is done."""
        plain = await self.plain_store(tmp_path)
        client = make_client(tmp_path)
        assert await client.list_buckets() == ["docs", "media"]
        assert await client.encrypt_existing() == 1
        assert await client.encrypt_existing(bucket="docs") == 1
        assert await client.encrypt_existing() == 0
        assert await client.encrypt_existing(bucket="docs") == 0
        assert b"PLAINTEXT-MARKER" not in raw(plain, "p.txt")
        assert await collect(await client.stream_object("p.txt")) == payload(BLOCK + 3)
        assert await collect(await client.stream_object("q.txt", bucket="docs")) == (
            b"short"
        )
        assert (await client.stat_object("p.txt")).content_type == "text/plain"

    async def test_encrypt_existing_leaves_encrypted_objects_alone(
        self, tmp_path: Path
    ) -> None:
        """An already encrypted file is not rewritten."""
        client = make_client(tmp_path)
        await client.put_object("a.bin", b"done")
        before = raw(client, "a.bin")
        assert await client.encrypt_existing() == 0
        assert raw(client, "a.bin") == before

    async def test_migration_needs_keys(self, tmp_path: Path) -> None:
        """A store without keys has nothing to encrypt with."""
        plain = await self.plain_store(tmp_path)
        with pytest.raises(ValueError, match="encryption_keys"):
            await plain.encrypt_existing()
        with pytest.raises(ValueError, match="encryption_keys"):
            await plain.reencrypt()


class TestConfiguration:
    def test_short_key_is_refused(self, tmp_path: Path) -> None:
        """AES-256 needs exactly 32 bytes."""
        with pytest.raises(ValueError, match="32 bytes"):
            make_client(tmp_path, encryption_keys={"k1": b"short"})

    def test_empty_key_id_is_refused(self, tmp_path: Path) -> None:
        """The id is stored in the header and cannot be empty."""
        with pytest.raises(ValueError, match="key ids"):
            make_client(tmp_path, encryption_keys={"": KEY_1})

    def test_error_names_the_id_not_the_key(self, tmp_path: Path) -> None:
        """A refused key never reaches the message."""
        secret = b"\x01" * 31
        with pytest.raises(ValueError) as caught:
            make_client(tmp_path, encryption_keys={"prod": secret})
        assert "prod" in str(caught.value)
        assert repr(secret) not in str(caught.value)

    def test_no_keys_means_no_encryption(self, tmp_path: Path) -> None:
        """``None`` and ``{}`` keep the plaintext store."""
        assert not make_client(tmp_path, encryption_keys=None).encrypted
        assert not make_client(tmp_path, encryption_keys={}).encrypted
        assert make_client(tmp_path).encrypted

    async def test_plain_client_writes_the_payload_as_is(self, tmp_path: Path) -> None:
        """Without keys the file on disk is the payload, byte for byte."""
        plain = LocalObjectStorageClient(
            tmp_path, public_url="https://x.example", signing_secret=SECRET
        )
        etag = await plain.put_object("a.bin", b"payload")
        assert raw(plain, "a.bin") == b"payload"
        assert etag == hashlib.md5(b"payload").hexdigest()

    def test_disabled_client_does_not_build_the_cipher(self, tmp_path: Path) -> None:
        """``enabled=False`` validates the keys but needs no cryptography."""
        client = make_client(tmp_path / "off", enabled=False)
        assert not client.encrypted
        assert not (tmp_path / "off").exists()
