from unittest import TestCase

from simpleais import *
import simpleais

fragmented_message_type_8 = ['!AIVDM,3,1,3,A,85NoHR1KfI99t:BHBI3sWpAoS7VHRblW8McQtR3lsFR,0*5A',
                      '!AIVDM,3,2,3,A,ApU6wWmdIeJG7p1uUhk8Tp@SVV6D=sTKh1O4fBvUcaN,0*5E',
                      '!AIVDM,3,3,3,A,j;lM8vfK0,2*34']


class TestBasicParsing(TestCase):
    def test_convenience_parse(self):
        self.assertEqual(None, simpleais.parse(''))
        self.assertEqual([], simpleais.parse([]))

    def test_empty(self):
        self.assertEqual(None, simpleais.parse_one(''))

    def test_basic_sentence(self):
        sentence = simpleais.parse('!ABVDM,1,1,,A,15NaEPPP01oR`R6CC?<j@gvr0<1C,0*1F')
        self.assertIsInstance(sentence, Sentence)
        self.assertEqual(Talker('AB'), sentence.talker)
        self.assertEqual(SentenceType('VDM'), sentence.sentence_type)
        self.assertEqual('A', sentence.radio_channel)
        self.assertEqual(1, sentence.type_id())

    def test_single_fragment(self):
        body = '85NoHR1KfI99t:BHBI3sWpAoS7VHRblW8McQtR3lsFR'
        padding = 0
        fragment = simpleais.parse('!AIVDM,3,1,3,A,%s,%s*5A' % (body, padding))
        self.assertIsInstance(fragment, SentenceFragment)
        self.assertEqual(len(body) * 6 - padding, len(fragment.bits()))

    def test_basic_lists(self):
        sentences = simpleais.parse([
            '!AIVDM,1,1,,B,H52R9E1<D<tpB1LTp@000000000,2*5C',
            '!ABVDM,1,1,,A,15MqdBP001GRT>>CCUu360Lr041d,0*69'
        ])
        self.assertEqual(2, len(sentences))

    def test_fragment_assembly(self):
        raw = fragmented_message_type_8
        sentences = simpleais.parse(raw)
        self.assertEqual(1, len(sentences))
        message_bytes = sum([len(m) - len('!AIVDM,3,1,3,A,') - len(',2*34') for m in raw])
        message_bits = message_bytes * 6 - 2  # where 2 is padding on last fragment
        self.assertEquals(message_bits, len(sentences[0].message_bits()))


class TestStreamParser(TestCase):
    def test_simple_use(self):
        p = StreamParser()
        self.assertFalse(p.hasSentence())
        p.add('!ABVDM,1,1,,A,15MqdBP001GRT>>CCUu360Lr041d,0*69')
        self.assertTrue(p.hasSentence())
        self.assertEqual(1, p.nextSentence().type_id())
        self.assertFalse(p.hasSentence())

    def test_buffer_results(self):
        p = StreamParser()
        self.assertFalse(p.hasSentence())
        p.add('!ABVDM,1,1,,A,15MqdBP001GRT>>CCUu360Lr041d,0*69')
        p.add('!ABVDM,1,1,,B,35NF6IPOiEoRe@HCBOS0VPeF0P00,0*54')
        self.assertTrue(p.hasSentence())
        self.assertEqual(1, p.nextSentence().type_id())
        self.assertEqual(3, p.nextSentence().type_id())
        self.assertFalse(p.hasSentence())

    def test_fragment_assembly(self):
        p = StreamParser()
        i = iter(fragmented_message_type_8)
        self.assertFalse(p.hasSentence())
        p.add(i.__next__())
        self.assertFalse(p.hasSentence())
        p.add(i.__next__())
        self.assertFalse(p.hasSentence())
        p.add(i.__next__())
        self.assertTrue(p.hasSentence())
        self.assertEqual(8, p.nextSentence().type_id())
        self.assertFalse(p.hasSentence())


class TestFragmentPool(TestCase):
    def __init__(self, method_name='runTest'):
        super(TestFragmentPool, self).__init__(method_name)
        self.raw_fragments = fragmented_message_type_8

        self.cooked_fragments = [parse_one(m) for m in self.raw_fragments]

    def test_empty(self):
        f = FragmentPool()
        self.assertFalse(f.has_full_sentence())
        with self.assertRaises(ValueError):
            f.pop_full_sentence()

    def test_partial_fill(self):
        f = FragmentPool()
        f.add(self.cooked_fragments[0])
        f.add(self.cooked_fragments[1])
        self.assertFalse(f.has_full_sentence())
        with self.assertRaises(Exception):
            f.pop_full_sentence()

    def test_normal_usage(self):
        f = FragmentPool()
        f.add(self.cooked_fragments[0])
        f.add(self.cooked_fragments[1])
        f.add(self.cooked_fragments[2])
        self.assertTrue(f.has_full_sentence())
        sentence = f.pop_full_sentence()

        # TODO: limit size


class TestNmeaPayload(TestCase):
    def test_basic_construction(self):
        body, fill_bits = '1', 0
        p = NmeaPayload(body, fill_bits)
        self.assertEqual(6, len(p))
        self.assertEqual('000001', p.bits.bin)

    def test_construction_from_bits(self):
        p = NmeaPayload(Bits(bin='0b000001'))
        self.assertEqual(6, len(p))
        self.assertEqual('000001', p.bits.bin)

    def test_padding(self):
        for fill_bits in range(0, 6):
            payload = NmeaPayload('w', fill_bits)
            self.assertEqual('111111'[0:(6 - fill_bits)], payload.bits.bin,
                             msg='failure for {} fill bits'.format(fill_bits))

    def test_full_message(self):
        body = '15NaEPPP01oR`R6CC?<j@gvr0<1C'
        p = NmeaPayload('%s' % body, 0)
        self.assertEqual(6 * len(body), len(p))
