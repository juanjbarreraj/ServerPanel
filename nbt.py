"""Minimal, exact NBT (Named Binary Tag) reader/writer.

Preserves everything byte-for-byte on round-trip:
- compound key order kept
- string bytes kept raw (Java modified UTF-8 safe)
- numeric tag types preserved via explicit wrappers
"""
import gzip, struct, io

TAG_END, TAG_BYTE, TAG_SHORT, TAG_INT, TAG_LONG, TAG_FLOAT, TAG_DOUBLE = 0, 1, 2, 3, 4, 5, 6
TAG_BYTE_ARRAY, TAG_STRING, TAG_LIST, TAG_COMPOUND, TAG_INT_ARRAY, TAG_LONG_ARRAY = 7, 8, 9, 10, 11, 12

TYPE_NAMES = {0:'End',1:'Byte',2:'Short',3:'Int',4:'Long',5:'Float',6:'Double',
              7:'ByteArray',8:'String',9:'List',10:'Compound',11:'IntArray',12:'LongArray'}

class Tag:
    __slots__ = ('t', 'v')
    def __init__(self, t, v):
        self.t = t
        self.v = v
    def __repr__(self):
        return f'Tag({TYPE_NAMES[self.t]}, {self.v!r})'

class NList:  # list payload: element type + items (items are raw payloads, not Tag)
    __slots__ = ('etype', 'items')
    def __init__(self, etype, items):
        self.etype = etype
        self.items = items
    def __repr__(self):
        return f'NList({TYPE_NAMES[self.etype]}, n={len(self.items)})'

# ---------- reading ----------

class _R:
    def __init__(self, data):
        self.d = data
        self.p = 0
    def take(self, n):
        b = self.d[self.p:self.p+n]
        if len(b) != n:
            raise ValueError('unexpected EOF')
        self.p += n
        return b
    def u1(self):  return self.take(1)[0]
    def i2(self):  return struct.unpack('>h', self.take(2))[0]
    def u2(self):  return struct.unpack('>H', self.take(2))[0]
    def i4(self):  return struct.unpack('>i', self.take(4))[0]
    def i8(self):  return struct.unpack('>q', self.take(8))[0]
    def f4(self):  return struct.unpack('>f', self.take(4))[0]
    def f8(self):  return struct.unpack('>d', self.take(8))[0]

def _read_payload(r, t):
    if t == TAG_BYTE:   return struct.unpack('>b', r.take(1))[0]
    if t == TAG_SHORT:  return r.i2()
    if t == TAG_INT:    return r.i4()
    if t == TAG_LONG:   return r.i8()
    if t == TAG_FLOAT:  return r.f4()
    if t == TAG_DOUBLE: return r.f8()
    if t == TAG_BYTE_ARRAY:
        n = r.i4(); return r.take(n)
    if t == TAG_STRING:
        n = r.u2(); return r.take(n)  # raw bytes
    if t == TAG_LIST:
        et = r.u1(); n = r.i4()
        return NList(et, [_read_payload(r, et) for _ in range(n)])
    if t == TAG_COMPOUND:
        items = []
        while True:
            tt = r.u1()
            if tt == TAG_END:
                break
            nl = r.u2(); name = r.take(nl)
            items.append([name, Tag(tt, _read_payload(r, tt))])
        return items
    if t == TAG_INT_ARRAY:
        n = r.i4(); return list(struct.unpack(f'>{n}i', r.take(4*n)))
    if t == TAG_LONG_ARRAY:
        n = r.i4(); return list(struct.unpack(f'>{n}q', r.take(8*n)))
    raise ValueError(f'bad tag type {t}')

def parse(raw):
    """raw = uncompressed NBT bytes. Returns (root_name_bytes, Tag(root))."""
    r = _R(raw)
    t = r.u1()
    if t != TAG_COMPOUND:
        raise ValueError(f'root tag type {t}, expected compound')
    nl = r.u2(); name = r.take(nl)
    root = Tag(TAG_COMPOUND, _read_payload(r, TAG_COMPOUND))
    if r.p != len(raw):
        raise ValueError(f'{len(raw)-r.p} trailing bytes')
    return name, root

# ---------- writing ----------

def _write_payload(w, t, v):
    if t == TAG_BYTE:   w.write(struct.pack('>b', v)); return
    if t == TAG_SHORT:  w.write(struct.pack('>h', v)); return
    if t == TAG_INT:    w.write(struct.pack('>i', v)); return
    if t == TAG_LONG:   w.write(struct.pack('>q', v)); return
    if t == TAG_FLOAT:  w.write(struct.pack('>f', v)); return
    if t == TAG_DOUBLE: w.write(struct.pack('>d', v)); return
    if t == TAG_BYTE_ARRAY:
        w.write(struct.pack('>i', len(v))); w.write(v); return
    if t == TAG_STRING:
        w.write(struct.pack('>H', len(v))); w.write(v); return
    if t == TAG_LIST:
        w.write(struct.pack('>Bi', v.etype, len(v.items)))
        for it in v.items:
            _write_payload(w, v.etype, it)
        return
    if t == TAG_COMPOUND:
        for name, tag in v:
            w.write(struct.pack('>B', tag.t))
            w.write(struct.pack('>H', len(name))); w.write(name)
            _write_payload(w, tag.t, tag.v)
        w.write(b'\x00')
        return
    if t == TAG_INT_ARRAY:
        w.write(struct.pack('>i', len(v))); w.write(struct.pack(f'>{len(v)}i', *v)); return
    if t == TAG_LONG_ARRAY:
        w.write(struct.pack('>i', len(v))); w.write(struct.pack(f'>{len(v)}q', *v)); return
    raise ValueError(f'bad tag type {t}')

def serialize(name, root):
    w = io.BytesIO()
    w.write(b'\x0a')
    w.write(struct.pack('>H', len(name))); w.write(name)
    _write_payload(w, TAG_COMPOUND, root.v)
    return w.getvalue()

# ---------- file helpers ----------

def load(path):
    """Returns (name, root, was_gzipped)."""
    blob = open(path, 'rb').read()
    gz = blob[:2] == b'\x1f\x8b'
    raw = gzip.decompress(blob) if gz else blob
    name, root = parse(raw)
    return name, root, gz

def save(path, name, root, gz=True):
    raw = serialize(name, root)
    if gz:
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0) as f:
            f.write(raw)
        raw = buf.getvalue()
    open(path, 'wb').write(raw)

# ---------- tree utilities ----------

def cget(comp_items, key):
    """Get Tag by name from compound payload (list of [name, Tag]). key: str."""
    kb = key.encode()
    for name, tag in comp_items:
        if name == kb:
            return tag
    return None

def ckeys(comp_items):
    return [name.decode('utf-8', 'replace') for name, tag in comp_items]

def cdel(comp_items, key):
    kb = key.encode()
    for i, (name, tag) in enumerate(comp_items):
        if name == kb:
            del comp_items[i]
            return True
    return False

def tree_diff(a, b, path=''):
    """Yield (path, description) for every difference between Tag a and Tag b."""
    if a.t != b.t:
        yield (path, f'type {TYPE_NAMES[a.t]} -> {TYPE_NAMES[b.t]}')
        return
    if a.t == TAG_COMPOUND:
        an = {bytes(n): t for n, t in a.v}
        bn = {bytes(n): t for n, t in b.v}
        for k in an.keys() | bn.keys():
            kp = f'{path}.{k.decode("utf-8","replace")}' if path else k.decode('utf-8','replace')
            if k not in bn:
                yield (kp, 'removed')
            elif k not in an:
                yield (kp, 'added')
            else:
                yield from tree_diff(an[k], bn[k], kp)
        # order changes (same keys, different order) — flag but only if sets equal
        if list(an.keys()) != [n for n, _ in b.v] and an.keys() == bn.keys():
            if [bytes(n) for n, _ in a.v] != [bytes(n) for n, _ in b.v]:
                yield (path or '<root>', 'key order changed')
    elif a.t == TAG_LIST:
        if a.v.etype != b.v.etype or len(a.v.items) != len(b.v.items):
            yield (path, f'list {TYPE_NAMES[a.v.etype]}[{len(a.v.items)}] -> {TYPE_NAMES[b.v.etype]}[{len(b.v.items)}]')
            return
        for i, (x, y) in enumerate(zip(a.v.items, b.v.items)):
            yield from tree_diff(Tag(a.v.etype, x), Tag(b.v.etype, y), f'{path}[{i}]')
    else:
        same = (a.v == b.v) if not isinstance(a.v, float) else (struct.pack('>d', a.v) == struct.pack('>d', b.v))
        if not same:
            av, bv = a.v, b.v
            if isinstance(av, bytes) and len(av) > 40: av = av[:40] + b'...'
            if isinstance(bv, bytes) and len(bv) > 40: bv = bv[:40] + b'...'
            yield (path, f'{av!r} -> {bv!r}')
