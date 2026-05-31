#!/usr/bin/env bash
# ehds-cpu-watchdog — notice sustained-high-CPU runaway processes.
#
# Detection-only by default: it logs a WARNING to the journal (tag
# `ehds-cpu-watchdog`) for any process that has burned a high lifetime-average
# %CPU for longer than a floor age. It does NOT kill anything.
#
# Built after an orphaned `ugrep -r ... /` left behind by a dead remote agent
# session pegged all 4 cores for 2.5 days unnoticed. Lifetime-average %CPU is
# used deliberately: a true runaway pegs cores continuously so its average
# stays high, while a brief legitimate spike (a test run, the ~7-min java
# validator) never accumulates a high average — and the min-age gate filters
# those out anyway. Tradeoff: a process that idles for days then spikes briefly
# won't trip it; that's fine for catching wedged loops, which is the point.
#
# Tunables (env):
#   EHDS_WATCHDOG_CPU        lifetime %CPU summed across cores  (default 80)
#   EHDS_WATCHDOG_MIN_ETIME  ignore processes younger than N s  (default 1800)
#   EHDS_WATCHDOG_KILL       if "1", SIGTERM flagged procs       (default 0/off)
set -euo pipefail

CPU_THRESHOLD="${EHDS_WATCHDOG_CPU:-80}"
MIN_ETIME_SECS="${EHDS_WATCHDOG_MIN_ETIME:-1800}"
KILL="${EHDS_WATCHDOG_KILL:-0}"
SELF_PID=$$

flagged=0
while read -r pid pcpu etimes user cmd; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    [[ "$pid" -eq "$SELF_PID" ]] && continue
    [[ "$pid" -eq "$PPID" ]] && continue          # the ps in the pipe / our shell parent
    # numeric float-safe compare: keep this row only if pcpu >= threshold
    awk -v c="$pcpu" -v t="$CPU_THRESHOLD" 'BEGIN{exit !(c+0 >= t+0)}' || continue
    [[ "$etimes" -ge "$MIN_ETIME_SECS" ]] || continue

    msg="runaway? pid=$pid cpu=${pcpu}% elapsed=${etimes}s user=$user cmd=${cmd}"
    logger -t ehds-cpu-watchdog -p daemon.warning -- "$msg"
    echo "WARN $msg"
    flagged=$((flagged + 1))

    if [[ "$KILL" == "1" ]]; then
        if kill -TERM "$pid" 2>/dev/null; then
            logger -t ehds-cpu-watchdog -p daemon.warning -- "sent SIGTERM to pid=$pid"
            echo "      -> sent SIGTERM to pid=$pid"
        fi
    fi
done < <(ps -eo pid=,pcpu=,etimes=,user=,args= --sort=-pcpu)

if [[ "$flagged" -eq 0 ]]; then
    echo "ok: no process >= ${CPU_THRESHOLD}% CPU for >= ${MIN_ETIME_SECS}s"
fi
exit 0
