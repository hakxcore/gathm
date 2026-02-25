import importlib.util
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PILOT_MAIN_PATH = PROJECT_ROOT / "pilot" / "main.py"
AGENT_SCRIPT = PROJECT_ROOT / "agent" / "orchestrator.sh"
TOOLS_DIR = PROJECT_ROOT / "tools"
VERSION_PATTERN = re.compile(r"\b\d+\.\d+(?:\.\d+)?\b")

# Most tools expose `-v`; googler uses `--version`.
TOOL_SMOKE_ARGS = {
    "googler": "--version",
}

COMPLEX_QUERY_CASES = [
    (
        "I'm planning a weekend in Seattle and need a detailed weather forecast including rain risk",
        "weather",
    ),
    (
        "Can you check if alice@example.com has been in any data breach leaks?",
        "pwned",
    ),
    (
        "Need a python cheatsheet for list comprehension and syntax examples",
        "cheat",
    ),
    (
        "Find latest headline updates about cybersecurity and AI",
        "news",
    ),
    (
        "Generate a QR code for https://example.com and explain decode options",
        "qrify",
    ),
    (
        "I want to geolocate my IP address and inspect WAN details",
        "geo",
    ),
    (
        "Please play some music in jukebox mode",
        "jukebox",
    ),
    (
        "Make me a funny meme image for a team joke",
        "meme",
    ),
    (
        "Show me song lyrics for Bohemian Rhapsody",
        "lyrics",
    ),
    (
        "Who is the registrar for example.com domain?",
        "rdap",
    ),
    (
        "Run a reverse DNS PTR lookup for 8.8.8.8",
        "rdns",
    ),
    (
        "Perform a whois lookup for openai.com registration details",
        "whois",
    ),
    (
        "Resolve MX records for gmail.com",
        "dns",
    ),
    (
        "Check TLS certificate expiry for github.com",
        "certinfo",
    ),
    (
        "Look up CVE-2024-3094 severity and summary",
        "cve",
    ),
    (
        "Probe HTTP status and headers for openai.com",
        "httpprobe",
    ),
    (
        "Is cloudflare.com DNSSEC enabled with DS and DNSKEY?",
        "dnssec",
    ),
    (
        "Find ASN and BGP prefix owner for 8.8.8.8",
        "asn",
    ),
    (
        "Run urlscan for suspicious paypal.com login URL",
        "urlscan",
    ),
    (
        "Detect WAF like cloudflare or akamai on example.com",
        "wafdetect",
    ),
    (
        "Enumerate subdomains for openai.com using certificate transparency",
        "subdomains",
    ),
    (
        "Run robots.txt audit and sitemap review for example.com",
        "robotsaudit",
    ),
    (
        "Audit security headers like HSTS and CSP for github.com",
        "headersaudit",
    ),
    (
        "Run subfinder passive subdomain discovery for openai.com",
        "subfinder",
    ),
    (
        "Use dnsx to probe DNS records for example.com",
        "dnsx",
    ),
    (
        "Use httpx web probe on openai.com",
        "httpx",
    ),
    (
        "Run naabu fast port scan on example.com",
        "naabu",
    ),
    (
        "Use katana web crawler for https://example.com",
        "katana",
    ),
    (
        "Run nuclei template scan against https://example.com",
        "nuclei",
    ),
    (
        "Use uncover search engine discovery for ssl:\\\"example.com\\\"",
        "uncover",
    ),
    (
        "Run pdchain recon workflow for example.com",
        "pdchain",
    ),
    (
        "Execute strix tool workflow",
        "strix",
    ),
    (
        "Run a tcp and udp portscan with custom port range on scanme.nmap.org",
        "portscan",
    ),
    (
        "Create Maltego entities CSV for domain graph investigation",
        "maltego",
    ),
    (
        "Check IOC reputation in VirusTotal and AbuseIPDB for 1.1.1.1",
        "tipcheck",
    ),
    (
        "Run image analysis with OCR and object detection on screenshot.png",
        "imganalyze",
    ),
]


def _load_pilot_module():
    spec = importlib.util.spec_from_file_location("pilot_main", PILOT_MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load pilot module from {PILOT_MAIN_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PILOT = _load_pilot_module()


class _HomeSandboxTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp_home = tempfile.TemporaryDirectory(prefix="gathm-tests-home-")
        cls._old_home = os.environ.get("HOME")
        os.environ["HOME"] = cls._temp_home.name

    @classmethod
    def tearDownClass(cls):
        if cls._old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = cls._old_home
        cls._temp_home.cleanup()


class TestPilotToolCoverage(_HomeSandboxTestCase):
    def test_discover_tools_matches_filesystem(self):
        discovered = sorted(PILOT.discover_tools())
        expected = sorted(
            path.name
            for path in TOOLS_DIR.iterdir()
            if path.is_dir() and (path / path.name).is_file()
        )
        self.assertListEqual(discovered, expected)

    def test_unknown_tool_returns_clear_error(self):
        output = PILOT.run_gathm_tool_raw("tool_that_does_not_exist")
        self.assertIn("Error: Tool 'tool_that_does_not_exist' not found.", output)

    def test_all_tools_have_smoke_command(self):
        tools = sorted(PILOT.discover_tools())
        self.assertGreater(len(tools), 0)

        for tool in tools:
            args = TOOL_SMOKE_ARGS.get(tool, "-v")
            command = f"{tool} {args}".strip()
            with self.subTest(tool=tool, command=command):
                output = PILOT.run_gathm_tool_raw(command)
                self.assertNotIn("Error: No tool specified.", output, msg=output)
                self.assertNotIn("Error: Tool", output, msg=output)
                self.assertNotIn("Invalid command syntax", output, msg=output)
                self.assertRegex(output, VERSION_PATTERN, msg=output)


class TestComplexQueryRouting(_HomeSandboxTestCase):
    def _ask_agent(self, query: str) -> dict:
        env = {
            **os.environ,
            "GATHM_OUTPUT_MODE": "json",
            "TERM": "xterm-256color",
        }
        result = subprocess.run(
            ["bash", str(AGENT_SCRIPT), "ask", query],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr.strip())
        self.assertTrue(result.stdout.strip(), msg="No JSON response from agent.")

        try:
            return json.loads(result.stdout.strip())
        except json.JSONDecodeError as exc:
            self.fail(f"Invalid JSON output: {exc}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")

    def test_complex_queries_route_to_expected_tools(self):
        for query, expected_tool in COMPLEX_QUERY_CASES:
            with self.subTest(query=query, expected_tool=expected_tool):
                payload = self._ask_agent(query)
                matched_tool = str(payload.get("matched_tool", "")).strip()
                self.assertEqual(matched_tool, expected_tool, msg=payload)

    def test_unmatched_complex_query_returns_null_tool(self):
        payload = self._ask_agent("Design a kanban workflow retrospective for sprint planning")
        self.assertIsNone(payload.get("matched_tool"), msg=payload)
        self.assertEqual(payload.get("confidence"), 0, msg=payload)


if __name__ == "__main__":
    unittest.main()
