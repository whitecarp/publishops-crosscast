"""The width and height a video shows at, read from its own file, never from a page. Only MP4 and MOV headers are read; for any other file the answer is None."""
import struct

LARGEST_HEADER = 64 << 20  # A movie header this big is no header this reads.


def boxes(data, start, end):
    """The boxes laid end to end in data[start:end], each as its kind and where its contents start and end. A box that runs past the end stops the list."""
    while start + 8 <= end:
        size, kind = struct.unpack_from('>I4s', data, start)
        head = 8
        if size == 1 and start + 16 <= end:
            size, head = struct.unpack_from('>Q', data, start + 8)[0], 16
        elif size == 0:
            size = end - start
        if size < head or start + size > end:
            return
        yield kind, start + head, start + size
        start += size


def movie_header(file):
    """What the file's moov box holds. Encoders put it before the media or after it, so each box before it is stepped over, not read."""
    file.seek(0, 2)
    end, start = file.tell(), 0
    while start + 8 <= end:
        file.seek(start)
        head = file.read(16)
        size, kind = struct.unpack_from('>I4s', head)
        skip = 8
        if size == 1 and len(head) == 16:
            size, skip = struct.unpack_from('>Q', head, 8)[0], 16
        elif size == 0:
            size = end - start
        if size < skip or start + size > end:
            return None
        if kind == b'moov':
            if size > LARGEST_HEADER:
                return None
            file.seek(start + skip)
            return file.read(size - skip)
        start += size
    return None


def frame_size(path):
    """(width, height) of the first video track as it is shown: turned a quarter when the track's matrix says so, as an upright phone video
    can be stored on its side. None when that can't be read."""
    try:
        with open(path, 'rb') as file:
            moov = movie_header(file) or b''
        for kind, start, end in boxes(moov, 0, len(moov)):
            if kind != b'trak':
                continue
            parts = {name: (begin, finish) for name, begin, finish in boxes(moov, start, end)}
            media = {name: (begin, finish) for name, begin, finish in boxes(moov, *parts.get(b'mdia', (0, 0)))}
            handler = media.get(b'hdlr')
            if b'tkhd' not in parts or not handler or moov[handler[0] + 8:handler[0] + 12] != b'vide':
                continue
            # After the header's times, its track number and duration, and the layer and volume, come its matrix and then its size.
            at = parts[b'tkhd'][0]
            at += 4 + (32 if moov[at] == 1 else 20) + 16
            a, b, _, c, d = struct.unpack_from('>5i', moov, at)
            width, height = (round(side / 65536) for side in struct.unpack_from('>II', moov, at + 36))
            if width and height:
                return (height, width) if a == 0 and d == 0 and b and c else (width, height)
    except (OSError, TypeError, ValueError, struct.error):
        return None
    return None
