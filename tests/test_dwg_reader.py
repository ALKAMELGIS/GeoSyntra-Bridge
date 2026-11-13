"""Built-in DWG reader: bit codes, LZ77 pages and the handle map."""
import struct

from geosyntra_bridge.cad.dwg.bits import BitReader
from geosyntra_bridge.cad.dwg.container import _magic, decompress18
from geosyntra_bridge.cad.dwg.objects import read_handles


def _bits(s: str) -> bytes:
    s = s.replace(" ", "")
    s += "0" * (-len(s) % 8)
    return int(s, 2).to_bytes(len(s) // 8, "big")


def test_bit_codes():
    # BS: 10 -> 0 ; 11 -> 256 ; 01 + byte 0x05 -> 5
    r = BitReader(_bits("10 11 01 00000101"))
    assert (r.BS(), r.BS(), r.BS()) == (0, 256, 5)
    # BD: 01 -> 1.0 ; 10 -> 0.0 ; 00 + raw double
    raw = "".join(f"{b:08b}" for b in struct.pack("<d", 12.5))
    r = BitReader(_bits("01 10 00" + raw))
    assert (r.BD(), r.BD(), r.BD()) == (1.0, 0.0, 12.5)
    # DD: 00 keeps the default
    assert BitReader(_bits("00")).DD(3.25) == 3.25
    # handle: code 0, 2 bytes 0x01 0x2C -> 300
    assert BitReader(_bits("0000 0010 00000001 00101100")).H() == 300
    # relative handle code 6 -> reference + 1
    assert BitReader(_bits("0110 0000")).H(41) == 42


def test_utf16_text():
    txt = "Layer-0".encode("utf-16-le")
    r = BitReader(_bits("01 00000111" + "".join(f"{b:08b}" for b in txt)))
    assert r.TV() == "Layer-0"


def test_header_magic_is_the_published_sequence():
    assert _magic(8) == bytes.fromhex("2923be84e16cd6ae")


def test_lz77_literal_only_page():
    # opcode 0x14 > 0x11: copy 0x14 - 17 = 3 literal bytes, then 0x11 ends the stream
    src = bytes([0x14]) + b"abc" + bytes([0x11])
    assert decompress18(src, 0, 3) == b"abc"


def test_handle_map():
    # one page: size 2 + payload, entries (handle +1, offset +100), (handle +2, offset +50), CRC, then end page
    payload = bytes([1, 0x80 | 100 & 0x7F, 0]) + bytes([2, 50])
    page = (len(payload) + 2).to_bytes(2, "big") + payload + b"\0\0"
    assert read_handles(page + (2).to_bytes(2, "big")) == {1: 100, 3: 150}
