"""Run a single market scan, then exit.

`python agent.py` loops forever (good for your own laptop).
GitHub Actions instead runs this file once every 15 minutes, so it scans
one time and stops. The scheduling is done by GitHub, not by a loop here.
"""

from agent import run_agent


if __name__ == "__main__":
    run_agent()
