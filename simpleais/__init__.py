import collections
from functools import reduce
from io import TextIOBase
import json
import logging
import os
import re
from time import sleep
from datetime import datetime

aivdm_pattern = re.compile(r'([.0-9]+)?\s*(![A-Z]{5},\d,\d,.?,[AB12],[^,]+,[0-5]\*[0-9A-F]{2})')


class Bits:
    def __init__(self, *args):
        if len(args) == 0:
            self.contents = ""
        elif len(args) == 1:
            if isinstance(args[0], str):
                self.contents = args[0]
            elif isinstance(args[0], int):
                self.contents = "{:b}".format(args[0])
            elif isinstance(args[0], Bits):
                self.contents = args[0].contents
            else:
                raise ValueError("don't know how to parse {}".format(args[0]))
        elif len(args) == 2 and isinstance(args[0], int):
            format_string = "{:0" + str(args[1]) + "b}"
            self.contents = format_string.format(args[0])
        else:
            raise ValueError("don't know how to parse {}, {}".format(args[0], args[1]))

    def append(self, other):
        if not isinstance(other, Bits):
            raise ValueError
        self.contents += other.contents

    def __int__(self):
        return int(self.contents, 2)

    def __getitem__(self, given):
        return Bits(self.contents.__getitem__(given))

    def __add__(self, other):
        return Bits(self.contents + other.contents)

    def __len__(self):
        return self.contents.__len__()

    def __eq__(self, other):
        return self.contents.__eq__(other.contents)

    def __str__(self):
        return self.contents

    def __repr__(self):
        return "Bits({})".format(str(self))

    @classmethod
    def join(cls, array):
        return Bits(''.join(b.contents for b in array))


class StreamParser:
    """
    Used to parse live streams of AIS messages.
    """

    def __init__(self):
        self.fragment_pool = {'A': FragmentPool(), 'B': FragmentPool()}
        self.sentence_buffer = collections.deque()

    def add(self, message_text):
        thing = parse_one(message_text)
        if isinstance(thing, Sentence):
            self.sentence_buffer.append(thing)
        elif isinstance(thing, SentenceFragment):
            pool = self.fragment_pool[thing.radio_channel]
            pool.add(thing)
            if pool.has_full_sentence():
                sentence = pool.pop_full_sentence()
                self.sentence_buffer.append(sentence)

    def next_sentence(self):
        return self.sentence_buffer.popleft()

    def has_sentence(self):
        return len(self.sentence_buffer) > 0


def parse_many(messages):
    p = StreamParser()
    result = []
    for m in messages:
        p.add(m)
        if p.has_sentence():
            result.append(p.next_sentence())
    return result


def parse_one(string):
    m = aivdm_pattern.search(string)
    if not m:
        return None

    if m.group(1):
        time = datetime.fromtimestamp(float(m.group(1)))
    else:
        time = None

    message = m.group(2)

    content, checksum = message[1:].split('*')
    fields = content.split(',')
    talker = Talker(fields[0][0:2])
    sentence_type = SentenceType(fields[0][2:])
    fragment_count = int(fields[1])
    radio_channel = fields[4]
    payload = NmeaPayload(fields[5], int(fields[6]))

    if fragment_count == 1:
        return Sentence(talker, sentence_type, radio_channel, payload, time, string)
    else:
        fragment_number = int(fields[2])
        message_id = int(fields[3])
        return SentenceFragment(talker, sentence_type, fragment_count, fragment_number,
                                message_id, radio_channel, payload, time, string)


def parse(message):
    if isinstance(message, list):
        return parse_many(message)
    else:
        return parse_one(message)


class NMEAThing:
    def __init__(self, text):
        self.text = text

    def __str__(self):
        return self.text

    def __repr__(self):
        return self.__str__()

    def __eq__(self, other):
        return (isinstance(other, self.__class__)
                and self.__dict__ == other.__dict__)

    def __ne__(self, other):
        return not self.__eq__(other)


class Talker(NMEAThing):
    pass


class SentenceType(NMEAThing):
    pass


def _make_nmea_lookup_table():
    lookup = {}
    for val in range(48, 88):
        lookup[chr(val)] = Bits(val - 48, 6)
    for val in range(96, 120):
        lookup[chr(val)] = Bits(val - 56, 6)
    return lookup


_nmea_lookup = _make_nmea_lookup_table()


# noinspection PyCallingNonCallable
class NmeaPayload:
    """
    Represents the decoded heart of an AIS message. The BitVector class used
    is not very fast and a bit rough, but is adequate for now. If performance
    becomes an issue, it might be worth replacing. See
    http://stackoverflow.com/questions/20845686/python-bit-array-performant
    for options.
    """

    def __init__(self, raw_data, fill_bits=0):
        if isinstance(raw_data, Bits):
            self.bits = raw_data
        else:
            self.bits = self._bits_for(raw_data, fill_bits)

    @staticmethod
    def _bits_for(ascii_representation, fill_bits):
        result = Bits()
        for pos in range(0, len(ascii_representation) - 1):
            result.append(_nmea_lookup[ascii_representation[pos]])
        bits_at_end = 6 - fill_bits
        selected_bits = _nmea_lookup[ascii_representation[-1]][0:bits_at_end]
        result.append(selected_bits)
        return result

    def __len__(self):
        return len(self.bits)


message_type_json = json.loads(open(os.path.join(os.path.dirname(__file__), 'aivdm.json')).read())['messages']


class Field:
    def __init__(self, name, start, end, data_type):
        self.name = name
        self.bit_range = slice(start, end + 1)
        if name == 'mmsi':
            self._decode = self._parse_mmsi
        elif name == 'lat':
            self._decode = self._parse_lat
        elif name == 'lon':
            self._decode = self._parse_lon
        elif data_type == 't':
            self._decode = self._parse_text
        elif data_type == 'I3':
            self._decode = lambda b: self._scaled_integer(b, 3)
        elif data_type == 'I4':
            self._decode = lambda b: self._scaled_integer(b, 4)
        elif data_type == 'u':
            self._decode = lambda b: int(b)
        elif data_type == 'U1':
            self._decode = lambda b: int(b) / 10.0
        elif data_type == 'e':
            self._decode = lambda b: "name {}".format(int(b))  # TODO: find and include enumerated types
        elif data_type == 'b':
            self._decode = lambda b: b == 1
        elif data_type == 'x':
            self._decode = lambda b: "ignored({})".format(int(b))
        else:
            raise ValueError("Sorry, don't know how to parse {} for field {} yet".format(data_type, name))

    def decode(self, bits):
        return self._decode(bits[self.bit_range])

    def _parse_mmsi(self, bits):
        return "%09i" % int(bits)

    def _parse_lat(self, bits):
        result = self._scaled_integer(bits, 4)
        if result != 91.0:
            return result

    def _parse_lon(self, bits):
        result = self._scaled_integer(bits, 4)
        if result != 181.0:
            return result

    def _twos_comp(self, val, length):
        if (val & (1 << (length - 1))) != 0:  # if sign bit is set e.g., 8bit: 128-255
            val = val - (1 << length)  # compute negative value
        return val

    def _scaled_integer(self, bits, scale):
        out = self._twos_comp(int(bits), len(bits))
        result = float(("%." + str(scale) + "f") % (out / 60.0 / (10 ** scale)))
        return result

    def _parse_text(self, bits):
        def chunks(s, n):
            for i in range(0, len(s), n):
                yield s[i:i + n]

        raw_ints = [int(nibble) for nibble in chunks(bits, 6)]
        mapped_ints = [i if i > 31 else i + 64 for i in raw_ints]
        text = ''.join([chr(i) for i in mapped_ints]).strip()
        text = text.rstrip('@').strip()
        return text



class Decoder:
    def __init__(self, message_info):
        self.fields = {}
        for field in message_info['fields']:
            name = field['member']
            self.fields[name] = Field(name, field['start'], field['end'], field['type'])

    def bit_range(self, name):
        return self.fields[name].bit_range

    def decode(self, name, bits):
        return self.fields[name].decode(bits)


DECODERS = {}
for message_type_id in range(1, 6):
    DECODERS[message_type_id] = Decoder(message_type_json[str(message_type_id)])


class SentenceFragment:
    def __init__(self, talker, sentence_type, total_fragments, fragment_number, message_id, radio_channel, payload,
                 time=None, text=None):
        self.talker = talker
        self.sentence_type = sentence_type
        self.total_fragments = total_fragments
        self.fragment_number = fragment_number
        self.message_id = message_id
        self.radio_channel = radio_channel
        self.payload = payload
        self.time = time
        self.text = text

    def initial(self):
        return self.fragment_number == 1

    def last(self):
        return self.fragment_number == self.total_fragments

    def key(self):
        key = (self.talker, self.sentence_type, self.total_fragments, self.message_id, self.radio_channel)
        return key

    def follows(self, other):
        return (self.fragment_number == other.fragment_number + 1) and self.key() == other.key()

    def bits(self):
        return self.payload.bits


class Sentence:
    def __init__(self, talker, sentence_type, radio_channel, payload, time=None, text=None):
        self.talker = talker
        self.sentence_type = sentence_type
        self.radio_channel = radio_channel
        self.payload = payload
        self.time = time
        self.text = text

    def type_id(self):
        return int(self.payload.bits[0:6])

    def message_bits(self):
        return self.payload.bits

    @classmethod
    def from_fragments(cls, matching_fragments):
        first = matching_fragments[0]
        message_bits = reduce(lambda a, b: a + b, [f.bits() for f in matching_fragments])
        text = [f.text for f in matching_fragments]
        return Sentence(first.talker, first.sentence_type, first.radio_channel, NmeaPayload(message_bits), first.time,
                        text)

    def __getitem__(self, item):
        return DECODERS[self.type_id()].decode(item, self.payload.bits)

    def _parse_mmsi(self, bits):
        return "%09i" % int(bits)

    def _parse_lat(self, bits):
        result = self._parse_latlong(bits)
        if result != 91.0:
            return result

    def _parse_lon(self, bits):
        result = self._parse_latlong(bits)
        if result != 181.0:
            return result

    def _parse_latlong(self, bits):
        def twos_comp(val, length):
            if (val & (1 << (length - 1))) != 0:  # if sign bit is set e.g., 8bit: 128-255
                val = val - (1 << length)  # compute negative value
            return val

        out = twos_comp(int(bits), len(bits))
        result = float("%.4f" % (out / 60.0 / 10000.0))
        return result



class FragmentPool:
    """
    A smart holder for SentenceFragments that can tell when
    a valid message has been found.
    """

    def __init__(self):
        self.fragments = []
        self.full_sentence = None

    def has_full_sentence(self):
        return self.full_sentence is not None

    def pop_full_sentence(self):
        if not self.full_sentence:
            raise ValueError
        result = self.full_sentence
        self.full_sentence = None
        return result

    def add(self, fragment):
        if len(self.fragments) > 0 and not fragment.follows(self.fragments[-1]):
            self.fragments.clear()
        self.fragments.append(fragment)
        if fragment.last():
            self.full_sentence = Sentence.from_fragments(self.fragments)
            self.fragments.clear()


def lines_from_source(source):
    if isinstance(source, TextIOBase):
        for line in source:
            yield line
    elif re.match("/dev/tty.*", source):
        yield from _handle_serial_source(source)
    elif re.match("https?://.*", source):
        yield from _handle_url_source(source)
    else:
        # assume it's a file
        yield from _handle_file_source(source)


def fragments_from_source(source):
    for line in lines_from_source(source):
        try:
            m = aivdm_pattern.search(line)
            if m:
                yield m.group(0)
            else:
                logging.getLogger().warn("skipped: \"{}\"".format(line.strip()))
        except Exception as e:
            print("failure", e, "for", line)


def sentences_from_source(source):
    parser = StreamParser()
    for fragment in fragments_from_source(source):
        try:
            parser.add(fragment)
            if parser.has_sentence():
                yield parser.next_sentence()
        except Exception as e:
            print("failure", e, "for", fragment)


# noinspection PyBroadException
def _handle_serial_source(source):
    import serial

    while True:
        try:
            with serial.Serial(source, 38400, timeout=10) as f:
                while True:
                    raw_line = f.readline()
                    try:
                        yield raw_line.decode('ascii')
                    except Exception as e:
                        print("weird input", raw_line, e)
        except Exception as e:
            print("unexpected failure", e)
            sleep(1)


def _handle_url_source(source):
    import urllib.request

    while True:
        try:
            with urllib.request.urlopen(source) as f:
                for line in f:
                    yield line.decode('utf-8')
        except Exception as e:
            print("unexpected failure", e)
            sleep(1)


def _handle_file_source(source):
    with open(source) as f:
        for line in f:
            yield line
