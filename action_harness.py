"""Harness for the !action agent.

Runs the real planner from bot.py (same ACTION_SYSTEM prompt, same model, same
json contract) against the Linux server, without touching Discord, and checks
what the model claims against what the server actually says.

The point is the false answers. A model that says "the service is running" is
only trustworthy if a command in its transcript showed that, so every run here
also fires an independent probe the model never sees and compares.

Examples
--------
    python action_harness.py "check whether debugger-chan is running"
    python action_harness.py --suite
    python action_harness.py --local "df -h"      # run commands here, not over ssh
    python action_harness.py --steps 8 "task"     # allow more steps

Env: OPENROUTER_API_KEY (required), SUDO_PASSWORD (for sudo steps),
     ACTION_HOST / ACTION_USER (default to the mint box),
     ACTION_PASSWORD (required for SSH runs; no default, never hardcode one).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import bot

DEFAULT_HOST = os.environ.get("ACTION_HOST", "192.168.15.203")
DEFAULT_USER = os.environ.get("ACTION_USER", "roland")
DEFAULT_PASSWORD = os.environ.get("ACTION_PASSWORD")  # required for SSH; no hardcoded default
REMOTE_DIR = "/home/roland/debugger-chan"

# Tasks with an independent probe. The probe output is what the truth is, and
# the model never sees it, so a matching answer is real evidence.
SUITE = [
    (
        "is the debugger-chan service active?",
        "systemctl is-active debugger-chan",
    ),
    (
        "how much free space is left on the / filesystem?",
        "df -h / | tail -1",
    ),
    (
        "how many lines does /home/roland/debugger-chan/bot.py have?",
        "wc -l < /home/roland/debugger-chan/bot.py",
    ),
    (
        "what is this machine's hostname?",
        "hostname",
    ),
    (
        "how many lines are in /etc/shadow? you will need sudo to read it",
        "sudo wc -l < /etc/shadow",
    ),
]

WORD = re.compile(r"[a-z0-9]{4,}")
NUMBER = re.compile(r"\d+")


class LocalRunner:
    """Commands run on this machine, in the action workspace."""

    name = "local"

    async def run(self, command: str) -> tuple[int, str]:
        bot.ACTION_WORKSPACE.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_shell(
            bot.shell_command(command, True),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(bot.ACTION_WORKSPACE),
            executable=bot.shell_executable(),
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), bot.ACTION_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, f"(timed out after {bot.ACTION_TIMEOUT}s)"
        return proc.returncode or 0, out.decode("utf-8", "replace")


class SSHRunner:
    """Commands run on the Mint server over ssh, encoded so quoting never bites."""

    name = "ssh"

    def __init__(self, host: str, user: str, password: str):
        import paramiko

        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(host, username=user, password=password, timeout=20)
        self.host = host

    def _exec(self, command: str) -> tuple[int, str]:
        payload = base64.b64encode(command.encode()).decode()
        wrapped = f"echo {payload} | base64 -d | bash"
        _, stdout, _ = self.client.exec_command(wrapped, timeout=bot.ACTION_TIMEOUT + 20)
        out = stdout.read().decode("utf-8", "replace")
        return stdout.channel.recv_exit_status(), out

    async def run(self, command: str) -> tuple[int, str]:
        return await asyncio.to_thread(self._exec, bot.shell_command(command, True))

    def close(self) -> None:
        self.client.close()


def shares_a_fact(answer: str, truth: str) -> bool:
    """True when the answer repeats something the real output actually says.

    Numbers are the strongest evidence - a model that invents "211 GB" without
    reading df is exactly the false answer this harness exists to catch - so when
    the truth has any, one of them has to appear in the answer.
    """
    said = answer.lower()
    numbers = NUMBER.findall(truth)
    if numbers:
        return any(number in said for number in numbers)
    return any(word in said for word in WORD.findall(truth.lower()))


async def drive(runner, task: str, max_steps: int) -> tuple[str, list[tuple[str, int, str]]]:
    """The real agent loop: plan, run, feed back, until done."""
    transcript = f"TASK: {task}\n"
    steps: list[tuple[str, int, str]] = []
    nudged = False

    for step in range(1, max_steps + 1):
        plan = await bot.ask_action_plan(transcript)
        if plan is None:
            return "(the model sent something that was not json)", steps

        if plan.get("done"):
            answer = str(plan.get("answer") or "").strip()
            if not steps and not nudged:
                nudged = True
                transcript += (
                    "\n[harness] you answered without running a single command, so that "
                    "answer is a guess. Run the command that proves it, read the output, "
                    "then answer.\n"
                )
                continue
            return answer, steps

        command = str(plan.get("command") or "").strip()
        if not command:
            return "(the model sent no command)", steps

        print(f"  [{step}] $ {command}")
        code, output = await runner.run(command)
        steps.append((command, code, output))
        print(f"      exit {code}")
        for line in (output.strip() or "(no output)").splitlines()[:8]:
            print(f"      | {line[:160]}")

        transcript += f"\n[step {step}] $ {command}\n(exit {code})\n"
        transcript += (output.strip() or "(no output)")[: bot.ACTION_OUTPUT_CHARS] + "\n"

    return "(ran out of steps)", steps


async def run_case(runner, task: str, probe: str | None, max_steps: int) -> bool:
    print(f"\nTASK: {task}")
    answer, steps = await drive(runner, task, max_steps)
    print(f"  answer: {answer}")

    truth = ""
    if probe:
        code, out = await runner.run(probe)
        truth = out.strip()
        print(f"  truth ({probe}): exit {code} {truth[:100]!r}")

    # A failed attempt the agent then fixed is fine; ending on a failure is not.
    ok = bool(steps) and steps[-1][1] == 0
    if probe and truth:
        ok = ok and shares_a_fact(answer, truth)
    answered_nothing = not answer or answer.startswith("(")
    ok = ok and not answered_nothing
    print(f"  {'PASS' if ok else 'FAIL'}")
    return ok


async def main() -> int:
    parser = argparse.ArgumentParser(description="exercise the !action agent")
    parser.add_argument("task", nargs="?", help="what to ask the agent")
    parser.add_argument("--suite", action="store_true", help="run the built-in cases")
    parser.add_argument("--local", action="store_true", help="run commands here")
    parser.add_argument("--steps", type=int, default=6, help="max model turns")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    args = parser.parse_args()

    if not args.task and not args.suite:
        parser.error("give a task or --suite")

    if not bot.get_env_optional("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY is missing - the agent cannot plan anything.")
        return 2

    if not args.local and not args.password:
        print("ACTION_PASSWORD is not set (or pass --password) - SSH runs need it.")
        return 2
    runner = LocalRunner() if args.local else SSHRunner(args.host, args.user, args.password)
    print(f"harness: {runner.name}  model: {bot.ACTION_MODEL}")
    print(f"sudo password set: {bool(bot.get_env_optional('SUDO_PASSWORD'))}")

    try:
        if args.suite:
            results = [
                await run_case(runner, task, probe, args.steps) for task, probe in SUITE
            ]
            print(f"\n{sum(results)}/{len(results)} cases passed")
            return 0 if all(results) else 1
        passed = await run_case(runner, args.task, None, args.steps)
        return 0 if passed else 1
    finally:
        if isinstance(runner, SSHRunner):
            runner.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
