#!/usr/bin/env python
# coding: utf-8

import click
import logging
import os
import random
import re
import subprocess
import sys
import time
import uuid
import yaml


logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))


TIMEOUT = 60 # 1 minute


class State(object):

    def __init__(self):
        self.interactive = True
        self.verify_status = False
        self.simulate_type = True
        self.next_step = 0

    def load(self):
        data = yaml.load(open("state.yaml"))
        self.interactive = bool(data["interactive"])
        self.verify_status = bool(data["verify_status"])
        self.simulate_type = bool(data["simulate_type"])
        self.next_step = int(data["next_step"])

    def save(self):
        with open("state.yaml", "w") as f:
            yaml.dump(dict(
                interactive=self.interactive,
                verify_status=self.verify_status,
                simulate_type=self.simulate_type,
                next_step=self.next_step), f, default_flow_style=False)


def hrule():
    return "="*int(subprocess.check_output(["tput", "cols"]))

# A "snippet" is something that the user is supposed to do in the workshop.
# Most of the "snippets" are shell commands.
# Some of them can be key strokes or other actions.
# In the markdown source, they are the code sections (identified by triple-
# quotes) within .exercise[] sections.

class Snippet(object):

    def __init__(self, slide, content):
        self.slide = slide
        self.content = content
        self.actions = []

    def __str__(self):
        return self.content


class Slide(object):

    current_slide = 0

    def __init__(self, content):
        Slide.current_slide += 1
        self.number = Slide.current_slide

        # Remove commented-out slides
        # (remark.js considers ??? to be the separator for speaker notes)
        content = re.split("\n\?\?\?\n", content)[0]
        self.content = content

        self.snippets = []
        exercises = re.findall("\.exercise\[(.*)\]", content, re.DOTALL)
        for exercise in exercises:
            if "```" in exercise:
                for snippet in exercise.split("```")[1::2]:
                    self.snippets.append(Snippet(self, snippet))
            else:
                logging.warning("Exercise on slide {} does not have any ``` snippet."
                                .format(self.number))
                self.debug()

    def __str__(self):
        text = self.content
        for snippet in self.snippets:
            text = text.replace(snippet.content, ansi("7")(snippet.content))
        return text

    def debug(self):
        logging.debug("\n{}\n{}\n{}".format(hrule(), self.content, hrule()))

# Synchronize slides in a remote browser
class Remote(object):

    def __init__(self):
        self.slide_on_screen = 0

    # Directly go to a specific slide
    def goto(self, slide_number):
        subprocess.check_call(["./gotoslide.js", str(slide_number)])
        self.slide_on_screen = slide_number
        focus_slides()

    # Offer the opportunity to go step by step to the given slide
    def catchup(self, slide_number):
        if self.slide_on_screen > slide_number:
            return self.goto(slide_number)
        while self.slide_on_screen < slide_number:
            if state.interactive:
                print("Catching up on slide: {} -> {}"
                    .format(self.slide_on_screen, slide_number))
                print("z/⏎     Zoom to target slide")
                print("n/→/⎵   Next slide")
                print("p/←     Previous slide")
                print("q       Abort remote control")
                command = click.getchar()
            else:
                command = "z"
            if command in ("z", "\r"):
                self.goto(slide_number)
            elif command in ("n", "\x1b[C", " "):
                self.goto(self.slide_on_screen+1)
            elif command in ("p", "\x1b[D"):
                self.goto(self.slide_on_screen-1)
            elif command == "q":
                return


def focus_slides():
    subprocess.check_call(["i3-msg", "workspace", "3"])
    subprocess.check_call(["i3-msg", "workspace", "1"])

def focus_terminal():
    subprocess.check_call(["i3-msg", "workspace", "2"])
    subprocess.check_call(["i3-msg", "workspace", "1"])

def focus_browser():
    subprocess.check_call(["i3-msg", "workspace", "4"])
    subprocess.check_call(["i3-msg", "workspace", "1"])


remote = Remote()
state = State()


def ansi(code):
    return lambda s: "\x1b[{}m{}\x1b[0m".format(code, s)


def wait_for_string(s, timeout=TIMEOUT):
    logging.debug("Waiting for string: {}".format(s))
    deadline = time.time() + timeout
    while time.time() < deadline:
        output = capture_pane()
        if s in output:
            return
        time.sleep(1)
    raise Exception("Timed out while waiting for {}!".format(s))


def wait_for_prompt():
    logging.debug("Waiting for prompt.")
    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        output = capture_pane()
        # If we are not at the bottom of the screen, there will be a bunch of extra \n's
        output = output.rstrip('\n')
        if output.endswith("\n$"):
            return
        if output.endswith("\n/ #"):
            return
        time.sleep(1)
    raise Exception("Timed out while waiting for prompt!")


def check_exit_status():
    if not state.verify_status:
        return
    token = uuid.uuid4().hex
    data = "echo {} $?\n".format(token)
    logging.debug("Sending {!r} to get exit status.".format(data))
    send_keys(data)
    time.sleep(0.5)
    wait_for_prompt()
    screen = capture_pane()
    status = re.findall("\n{} ([0-9]+)\n".format(token), screen, re.MULTILINE)
    logging.debug("Got exit status: {}.".format(status))
    if len(status) == 0:
        raise Exception("Couldn't retrieve status code {}. Timed out?".format(token))
    if len(status) > 1:
        raise Exception("More than one status code {}. I'm seeing double! Shoot them both.".format(token))
    code = int(status[0])
    if code != 0:
        raise Exception("Non-zero exit status: {}.".format(code))
    # Otherwise just return peacefully.


def setup_tmux_and_ssh():
    if subprocess.call(["tmux", "has-session"]):
        logging.error("Couldn't connect to tmux. Please setup tmux first.")
        ipaddr = open("../../prepare-vms/ips.txt").read().split("\n")[0]
        uid = os.getuid()

        raise Exception("""
1. If you're running this directly from a node:

tmux

2. If you want to control a remote tmux:

rm -f /tmp/tmux-{uid}/default && ssh -t -L /tmp/tmux-{uid}/default:/tmp/tmux-1001/default docker@{ipaddr} tmux

3. If you cannot control a remote tmux:

tmux new-session ssh docker@{ipaddr}
""".format(uid=uid, ipaddr=ipaddr))
    else:
        logging.info("Found tmux session. Trying to acquire shell prompt.")
        wait_for_prompt()
    logging.info("Successfully connected to test cluster in tmux session.")



slides = []
content = open(sys.argv[1]).read()

# OK, this part is definitely hackish, and will break if the
# excludedClasses parameter is not on a single line.
excluded_classes = re.findall("excludedClasses: (\[.*\])", content)
excluded_classes = set(eval(excluded_classes[0]))

for slide in re.split("\n---?\n", content):
    slide_classes = re.findall("class: (.*)", slide)
    if slide_classes:
        slide_classes = slide_classes[0].split(",")
        slide_classes = [c.strip() for c in slide_classes]
    if excluded_classes & set(slide_classes):
        logging.info("Skipping excluded slide.")
        continue
    slides.append(Slide(slide))

actions = []
for slide in slides:
    for snippet in slide.snippets:
        content = snippet.content
        # Extract the "method" (e.g. bash, keys, ...)
        # On multi-line snippets, the method is alone on the first line
        # On single-line snippets, the data follows the method immediately
        if '\n' in content:
            method, data = content.split('\n', 1)
        else:
            method, data = content.split(' ', 1)
        actions.append((slide, snippet, method, data))


def send_keys(data):
    if state.simulate_type and data[0] != '^':
        for key in data:
            if key == ";":
                key = "\\;"
            if key == "\n":
                time.sleep(1)
            subprocess.check_call(["tmux", "send-keys", key])
            time.sleep(0.2*random.random())
            if key == "\n":
                time.sleep(1)
    else:
        subprocess.check_call(["tmux", "send-keys", data])

def capture_pane():
    return subprocess.check_output(["tmux", "capture-pane", "-p"]).decode('utf-8')


setup_tmux_and_ssh()


try:
    state.load()
    logging.info("Successfully loaded state from file.")
    # Let's override the starting state, so that when an error occurs,
    # we can restart the auto-tester and then single-step or debug.
    # (Instead of running again through the same issue immediately.)
    state.interactive = True
except Exception as e:
    logging.exception("Could not load state from file.")
    logging.warning("Using default values.")


while state.next_step < len(actions):
    state.save()

    slide, snippet, method, data = actions[state.next_step]

    # Remove extra spaces (we don't want them in the terminal) and carriage returns
    data = data.strip()

    # Synchronize the remote slides
    remote.catchup(slide.number)

    print(hrule())
    print(slide.content.replace(snippet.content, ansi(7)(snippet.content)))
    print(hrule())
    if state.interactive:
        print("simulate_type:{} verify_status:{}".format(state.simulate_type, state.verify_status))
        print("[{}/{}] Shall we execute that snippet above?".format(state.next_step, len(actions)))
        print("y/⎵/⏎   Execute snippet")
        print("p/←     Previous snippet")
        print("n/→     Next snippet")
        print("s       Simulate keystrokes")
        print("v       Validate exit status")
        print("g       Go to a specific snippet")
        print("q       Quit")
        print("c       Continue non-interactively until next error")
        command = click.getchar()
    else:
        command = "y"

    # For now, remove the `highlighted` sections
    # (Make sure to use $() in shell snippets!)
    if '`' in data:
        logging.info("Stripping ` from snippet.")
        data = data.replace('`', '')

    if command in ("n", "\x1b[C"):
        state.next_step += 1
    elif command in ("p", "\x1b[D"):
        state.next_step -= 1
    elif command == "s":
        state.simulate_type = not state.simulate_type
    elif command == "v":
        state.verify_status = not state.verify_status
    elif command == "g":
        state.next_step = click.prompt("Enter snippet number", type=int)
        # Special case: if we go to snippet 0, also reset the slides deck
        if state.next_step == 0:
            remote.goto(1)
    elif command == "q":
        break
    elif command == "c":
        # continue until next timeout
        state.interactive = False
    elif command in ("y", "\r", " "):
        focus_terminal()
        logging.info("Running with method {}: {}".format(method, data))
        if method == "keys":
            send_keys(data)
        elif method == "bash":
            # Make sure that we're ready
            wait_for_prompt()
            # Strip leading spaces
            data = re.sub("\n +", "\n", data)
            # Add "RETURN" at the end of the command :)
            data += "\n"
            # Send command
            send_keys(data)
            # Force a short sleep to avoid race condition
            time.sleep(0.5)
            _, _, next_method, next_data = actions[state.next_step+1]
            if next_method == "wait":
                wait_for_string(next_data)
            elif next_method == "longwait":
                wait_for_string(next_data, 10*TIMEOUT)
            else:
                wait_for_prompt()
                # Verify return code FIXME should be optional
                check_exit_status()
        elif method == "copypaste":
            screen = capture_pane()
            matches = re.findall(data, screen, flags=re.DOTALL)
            if len(matches) == 0:
                raise Exception("Could not find regex {} in output.".format(data))
            # Arbitrarily get the most recent match
            match = matches[-1]
            # Remove line breaks (like a screen copy paste would do)
            match = match.replace('\n', '')
            send_keys(match + '\n')
            # FIXME: we should factor out the "bash" method
            wait_for_prompt()
            check_exit_status()
        elif method == "open":
            # Cheap way to get node1's IP address
            screen = capture_pane()
            ipaddr = re.findall("^\[(.*)\]", screen, re.MULTILINE)[-1]
            url = data.replace("/node1", "/{}".format(ipaddr))
            # This should probably be adapted to run on different OS
            subprocess.check_call(["xdg-open", url])
            focus_browser()
            if status.interactive:
                print("Press any key to continue to next step...")
                click.getchar()
        else:
            logging.warning("Unknown method {}: {!r}".format(method, data))
        state.next_step += 1

    else:
        logging.warning("Unknown command {}.".format(command))
