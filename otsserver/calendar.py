# Copyright (C) 2016 The OpenTimestamps developers
#
# This file is part of the OpenTimestamps Server.
#
# It is subject to the license terms in the LICENSE file found in the top-level
# directory of this distribution.
#
# No part of the OpenTimestamps Server including this file, may be copied,
# modified, propagated, or distributed except according to the terms contained
# in the LICENSE file.

import leveldb
import logging
import os
import queue
import struct
import sys
import threading
import time

from opentimestamps.core.notary import PendingAttestation, BitcoinBlockHeaderAttestation
from opentimestamps.core.op import OpPrepend, OpAppend, OpSHA256
from opentimestamps.core.serialize import StreamSerializationContext, StreamDeserializationContext, DeserializationError
from opentimestamps.core.timestamp import Timestamp
from opentimestamps.timestamp import make_merkle_tree, nonce_timestamp

from bitcoin.core import b2x, b2lx


class Journal:
    """Append-only commitment storage

    The journal exists simply to make sure we never lose a commitment.
    """
    COMMITMENT_SIZE = 32 + 4

    def __init__(self, path):
        self.read_fd = open(path, "rb")

    def __getitem__(self, idx):
        self.read_fd.seek(idx * self.COMMITMENT_SIZE)
        commitment = self.read_fd.read(self.COMMITMENT_SIZE)

        if len(commitment) == self.COMMITMENT_SIZE:
            return commitment
        else:
            raise KeyError()


class JournalWriter(Journal):
    """Writer for the journal"""
    def __init__(self, path):
        self.append_fd = open(path, "ab")

        # In case a previous write partially failed, seek to a multiple of the
        # commitment size
        logging.info("Opening journal for appending...")
        pos = self.append_fd.tell()

        if pos % self.COMMITMENT_SIZE:
            logging.error("Journal size not a multiple of commitment size; %d bytes excess; writing padding" % (pos % self.COMMITMENT_SIZE))
            self.append_fd.write(b'\x00'*(self.COMMITMENT_SIZE - (pos % self.COMMITMENT_SIZE)))

        logging.info("Journal has %d entries" % (self.append_fd.tell() // self.COMMITMENT_SIZE))

    def submit(self, commitment):
        """Add a new commitment to the journal

        Returns only after the commitment is syncronized to disk.
        """
        if len(commitment) != self.COMMITMENT_SIZE:
            raise ValueError("Journal commitments must be exactly %d bytes long" % self.COMMITMENT_SIZE)

        assert (self.append_fd.tell() % self.COMMITMENT_SIZE) == 0
        self.append_fd.write(commitment)
        self.append_fd.flush()
        os.fsync(self.append_fd.fileno())

class LevelDbCalendar:
    def __init__(self, path):
        self.db = leveldb.LevelDB(path)

    def __contains__(self, msg):
        try:
            self.db.Get(msg)
            return True
        except KeyError:
            return False

    def __get_timestamp(self, msg):
        """Get a timestamp, non-recursively"""
        serialized_timestamp = self.db.Get(msg)

        timestamp = Timestamp(msg)

        ctx = BytesDeserializationContext(serialized_timestamp)
        while ctx.fd.tell() < len(serialized_timestamp):
            op = Op.deserialize(msg, deserialize_timestamp=False)
            timestamp.add_op(op)

        return timestamp

    def __getitem__(self, msg):
        """Get the timestamp for a given message"""
        timestamp = self.__get_timestamp(msg)

        for op in timestamp:
            try:
                result = op.result
            except AttributeError:
                continue

            op.timestamp = self.__get_timestamp(op.result)

        return timestamp

    def __add_timestamp(self, new_timestamp, batch):
        try:
            existing_timestamp = self.__get_timestamp(new_timestamp.msg)
        except KeyError:
            existing_timestamp = Timestamp(new_timestamp.msg)

        if existing_timestamp == new_timestamp:
            # Note how because we didn't get the existing timestamp
            # recursively, the only way old and new can be identical is if all
            # the ops are verify operations.
            return

        modified = False
        existing_ops = 
        for op in new_timestamp:
            if op 

    def add(self, new_timestamp):
        batch = self.db.WriteBatch()
        self.__add_timestamp(new_timestamp, self.db.WriteBatch())
        self.db.Write(batch, sync = True)

class Calendar:
    def __init__(self, path):
        path = os.path.normpath(path)
        os.makedirs(path, exist_ok=True)
        self.path = path
        self.journal = JournalWriter(path + '/journal')

        self.db = leveldb.LeveLB(path + '/db')

        try:
            uri_path = self.path + '/uri'
            with open(uri_path, 'rb') as fd:
                self.uri = fd.read().strip()
        except FileNotFoundError as err:
            logging.error('Calendar URI not yet set; %r does not exist' % uri_path)
            sys.exit(1)

    def submit(self, submitted_commitment):
        serialized_time = struct.pack('>L', int(time.time()))

        commitment = submitted_commitment.ops.add(OpPrepend(serialized_time))
        commitment.attestations.add(PendingAttestation(self.uri))

        self.journal.submit(commitment.msg)

    def __contains__(self, commitment):
        try:
            next(self[commitment])
        except KeyError:
            return False
        return True

    def __getitem__(self, commitment):
        """Get commitment timestamps(s)"""
        commitment_path = self.__commitment_timestamps_path(commitment)
        try:
            timestamps = os.listdir(commitment_path)
        except FileNotFoundError:
            raise KeyError("No such commitment")

        if not timestamps:
            # An empty directory should fail too
            raise KeyError("No such commitment")

        no_valid_timestamps = True
        for timestamp_filename in sorted(timestamps):
            timestamp_path = commitment_path + '/' + timestamp_filename
            with open(timestamp_path, 'rb') as timestamp_fd:
                ctx = StreamDeserializationContext(timestamp_fd)
                try:
                    timestamp = Timestamp.deserialize(ctx, commitment)
                except DeserializationError as err:
                    logging.error("Bad commitment timestamp %r, err %r" % (timestamp_path, err))
                    continue

                no_valid_timestamps = False
                yield timestamp
        if no_valid_timestamps:
            raise KeyError("No such commitment")

    def __commitment_verification_path(self, commitment, verify_op):
        """Return the path for a specific timestamp"""
        # assuming bitcoin timestamp...
        assert verify_op.attestation.__class__ == BitcoinBlockHeaderAttestation
        return (self.__commitment_timestamps_path(commitment) +
                '/btcblk-%07d-%s' % (verify_op.attestation.height, b2lx(verify_op.msg)))

    def add_commitment_timestamp(self, timestamp):
        """Add a timestamp for a commitment"""
        path = self.__commitment_timestamps_path(timestamp.msg)
        os.makedirs(path, exist_ok=True)

        for msg, attestation in timestamp.all_attestations():
            # FIXME: we shouldn't ever be asked to open a file that aleady
            # exists, but we should handle it anyway
            with open(self.__commitment_verification_path(msg, attestation), 'xb') as fd:
                ctx = StreamSerializationContext(fd)
                timestamp.serialize(ctx)

                fd.flush()
                os.fsync(fd.fileno())


class Aggregator:
    def __loop(self):
        logging.info("Starting aggregator loop")
        while True:
            time.sleep(self.commitment_interval)

            digests = []
            done_events = []
            last_commitment = time.time()
            while not self.digest_queue.empty():
                # This should never raise the Empty exception, as we should be
                # the only thread taking items off the queue
                (digest, done_event) = self.digest_queue.get_nowait()
                digests.append(digest)
                done_events.append(done_event)

            if not len(digests):
                continue

            digests_commitment = make_merkle_tree(digests)

            logging.info("Aggregated %d digests under commitment %s" % (len(digests), b2x(digests_commitment.msg)))

            self.calendar.submit(digests_commitment)

            # Notify all requestors that the commitment is done
            for done_event in done_events:
                done_event.set()

    def __init__(self, calendar, commitment_interval=1):
        self.calendar = calendar
        self.commitment_interval = commitment_interval
        self.digest_queue = queue.Queue()
        self.thread = threading.Thread(target=self.__loop)
        self.thread.start()

    def submit(self, msg):
        """Submit message for aggregation

        Aggregator thread will aggregate the message along with all other
        messages, and return a Timestamp
        """
        timestamp = Timestamp(msg)

        # Add nonce to ensure requestor doesn't learn anything about other
        # messages being committed at the same time, as well as to ensure that
        # anything we store related to this commitment can't be controlled by
        # them.
        done_event = threading.Event()
        self.digest_queue.put((nonce_timestamp(timestamp), done_event))

        done_event.wait()

        return timestamp
