#!/usr/bin/env python3
##
# Copyright (c) OpenLoop, 2016
#
# This material is proprietary of The OpenLoop Alliance and its members.
# All rights reserved.
# The methods and techniques described herein are considered proprietary
# information. Reproduction or distribution, in whole or in part, is forbidden
# except by express written permission of OpenLoop.
#
# Source that is published publicly is for demonstration purposes only and
# shall not be utilized to any extent without express written permission of
# OpenLoop.
#
# Please see http://www.opnlp.co for contact information
##
import time
import socket
import logging
import argparse
import sys
import select
import threading
import traceback
from ansi import Ansi
from datetime import datetime, timedelta

MAX_MESSAGE_SIZE = 2048
PING_TIMEOUT = timedelta(seconds=1)

PROMPT_TRACK = 0
LAST_PROMPT = ""


def progress():
    global PROMPT_TRACK
    PROMPT_TRACK = (PROMPT_TRACK + 1) % 5
    return "." * PROMPT_TRACK + " " * (4-PROMPT_TRACK)


class PodStateType(type):
    MAP = {
        'POST': 0,
        'BOOT': 1,
        'LPFILL': 2,
        'HPFILL': 3,
        'LOAD': 4,
        'STANDBY': 5,
        'ARMED': 6,
        'PUSHING': 7,
        'COASTING': 8,
        'BRAKING': 9,
        'VENT': 10,
        'RETRIEVAL': 11,
        'EMERGENCY': 12,
        'SHUTDOWN': 13
    }

    SHORT_MAP = {
        'POST': 0,
        'BOOT': 1,
        'LPFL': 2,
        'HPFL': 3,
        'LOAD': 4,
        'STBY': 5,
        'ARMD': 6,
        'PUSH': 7,
        'COAS': 8,
        'BRKE': 9,
        'VENT': 10,
        'RETR': 11,
        'EMRG': 12,
        'SDWN': 13
    }

    def __getattr__(cls, name):
        if name in cls.MAP:
            return cls.MAP[name]
        raise AttributeError(name)


class PodState(metaclass=PodStateType):
    def __init__(self, state):
        self._state = int(state)

    def is_fault(self):
        return self._state == PodState.EMERGENCY

    def is_moving(self):
        return self._state in (PodState.BRAKING, PodState.COASTING,
                               PodState.PUSHING)

    def __str__(self):
        keys = [key for key, val in PodState.MAP.items() if val == self._state]
        if not keys:
            return "UNKNOWN"
        else:
            return keys[0]

    def short(self):
        keys = [key for key, val in PodState.SHORT_MAP.items()
                if val == self._state]
        if not keys:
            return "----"
        else:
            return keys[0]


class Pod:
    def __init__(self, addr):
        self.sock = None
        self.addr = addr
        self.last_ping = datetime.now()
        self.recieved = 0
        self.state = None

    def ping(self, _):
        self.send("ping")

        timed_out = (datetime.now() - self.last_ping > PING_TIMEOUT)
        if self.is_connected() and timed_out and self.recieved > 0:
            print(Ansi.make_bold(Ansi.make_red("PING TIMEOUT!")))
            self.close()

    def handle_data(self, data):
        if "PONG" in data:
            self.last_ping = datetime.now()
            self.recieved += 1
            state = PodState(data.split(":")[1])
            if self.state is None or state._state != self.state._state:
                sys.stdout.write("\r")

                self.state = state
                user_write(make_prompt(self))
            self.state = state
        else:
            sys.stdout.write(data)

        return "PONG" not in data

    def command(self, cmd):
        self.send(cmd + "\n")

    def transcribe(self, data):
        logging.info("[DATA] {}".format(data))

    def send(self, data):
        if not self.is_connected():
            return

        try:
            self.sock.send(data.encode('utf-8'))
        except Exception as e:
            logging.error(e)
            self.close()

    def recv(self):
        if not self.is_connected():
            return

        try:
            return self.sock.recv(MAX_MESSAGE_SIZE).decode('utf-8')
        except Exception as e:
            logging.error(e)
            self.close()

    def connect(self):
        try:
            self.sock = socket.create_connection(self.addr, 1)

            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

            self.recieved = 0
            self.last_ping = datetime.now()
        except Exception as e:
            self.close()
            raise e

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def is_connected(self):
        return self.sock is not None and self.sock.fileno() >= 0


class Heart:
    def __init__(self, interval, callback):
        self.interval = interval
        self.callback = callback
        self.running = False

    def start(self):
        self.running = True
        while self.running:
            self.callback(self)
            time.sleep(self.interval)

    def stop(self):
        self.running = False


def user_write(txt):
    sys.stdout.write(txt)
    sys.stdout.flush()


def make_prompt(pod, extra="> "):
    global LAST_PROMPT

    details = "%s:%d" % pod.addr
    if pod.state is not None:
        details += " %s" % pod.state.short()
        if pod.state.is_fault():
            details = Ansi.make_red(details)
        else:
            details = Ansi.make_green(details)
    else:
        walker = progress()
        details = Ansi.make_yellow(details + " " + walker)

    text = Ansi.make_bold("Pod(%s)%s" % (details, extra))
    raw = Ansi.strip(text)

    if len(Ansi.strip(LAST_PROMPT)) > len(raw):
        text += " " * (len(LAST_PROMPT) - len(raw))

    LAST_PROMPT = text.rstrip()
    return text


def loop(pod):
    user_write(make_prompt(pod))

    while not pod.is_connected():
        try:
            logging.debug("Attempting Connection")
            pod.connect()
        except Exception as e:
            logging.debug("Connection Exception: %s" % e)
            user_write("\r")
            user_write(make_prompt(pod, " ! %s " % e))
        time.sleep(1)
    logging.debug("Connected")

    pod.command("help")

    while pod.is_connected():
        (ready, _, _) = select.select([pod.sock, sys.stdin], [], [], 0.1)

        if pod.sock in ready:
            data = pod.recv()
            if data and pod.handle_data(data):
                user_write(make_prompt(pod))

        if sys.stdin in ready:
            try:
                cmd = input()
            except EOFError:
                logging.debug("EOF")
                sys.exit(0)
            pod.command(cmd)


def main():
    parser = argparse.ArgumentParser(description="Openloop Command Client",
                                     add_help=False)

    parser.add_argument("-v", "--verbose", action="store_true", default=False)

    parser.add_argument("-p", "--port", type=int, default=7779,
                        help="Pod server port")

    parser.add_argument("-h", "--host", default="127.0.0.1",
                        help="Pod server hostname")

    parser.add_argument("-i", "--heartbeat-interval", default="200", type=int,
                        help="heartbeat interval (ms)")

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
        logging.debug("Debug Logging Enabled")
    else:
        logging.basicConfig(level=logging.WARN)

    pod = Pod((args.host, args.port))

    heart = Heart(args.heartbeat_interval / 1000.0, pod.ping)

    threading.Thread(target=heart.start).start()

    while True:
        try:
            loop(pod)
        except SystemExit:
            heart.stop()
            raise
        except KeyboardInterrupt:
            print("Keyboard Interupt... shutdown")
            heart.stop()
            sys.exit(1)

        except IOError as e:
            print("[IOERROR] %s" % e)
        except Exception as e:
            print("[ERROR] %s" % e)
            traceback.print_exc()


if "__main__" == __name__:
    main()
