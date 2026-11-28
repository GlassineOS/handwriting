#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# glassine-train on a GPU server, in a container (README.md here).
#
#   run.sh build                          the image, from this checkout
#   run.sh unpack BUNDLE.tar              check and unpack a bundle into --data
#   run.sh selftest                       ~1 min: 40 steps on the GPU, CPU read-back, export
#   run.sh bench [-- TRAIN ARGS]          at most 10 min, 300 steps: prints steps/s
#   run.sh train [-- TRAIN ARGS]          the bundle's run (its arguments, then TRAIN ARGS)
#   run.sh logs | stop | ps               follow, stop (last.pt stays: --resume) or list
#   run.sh serve RUN                      the status page again for a finished run
#   run.sh clean [--bundle BUNDLE.tar]    delete --data (and the tarball); runs keep the model and logs
#
# Options (before the command's own arguments; defaults leave room for other work):
#   --data DIR        the unpacked bundle      (default ~/glassine-train/data)
#   --out DIR         the runs                 (default ~/glassine-train/out)
#   --cpus N          CPU cap                  (default 8)
#   --memory SIZE     memory cap, no swap      (default 16g)
#   --shm SIZE        /dev/shm for the loader  (default 2g)
#   --nice N          niceness of the training (default 10)
#   --threads N       torch CPU threads        (default 4)
#   --workers N       data loader processes    (default 4)
#   --gpus SPEC       docker --gpus            (default all)
#   --cpu-only        no GPU: --device cpu (a test of the kit on any machine)
#   --status-port P   status page port         (default 8686; 0 = none)
#   --status-bind A   host address it is published on (default 127.0.0.1; anything else warns)
#   --linger S        seconds the page stays up after the job (default 86400)
#   --image NAME      (default glassine-train-gpu)
#   --name NAME       container name           (default glassine-train)
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../.." && pwd)
DOCKER=${DOCKER:-docker}
DATA=$HOME/glassine-train/data
OUT=$HOME/glassine-train/out
CPUS=8 MEMORY=16g SHM=2g NICE=10 THREADS=4 WORKERS=4 GPUS=all CPU_ONLY=0
PORT=8686 BIND=127.0.0.1 LINGER=86400 IMAGE=glassine-train-gpu NAME=glassine-train

die() { echo "run.sh: $*" >&2; exit 2; }
usage() { sed -n '3,30p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case $1 in
    --data) DATA=$2; shift 2 ;;
    --out) OUT=$2; shift 2 ;;
    --cpus) CPUS=$2; shift 2 ;;
    --memory) MEMORY=$2; shift 2 ;;
    --shm) SHM=$2; shift 2 ;;
    --nice) NICE=$2; shift 2 ;;
    --threads) THREADS=$2; shift 2 ;;
    --workers) WORKERS=$2; shift 2 ;;
    --gpus) GPUS=$2; shift 2 ;;
    --cpu-only) CPU_ONLY=1; shift ;;
    --status-port) PORT=$2; shift 2 ;;
    --status-bind) BIND=$2; shift 2 ;;
    --linger) LINGER=$2; shift 2 ;;
    --image) IMAGE=$2; shift 2 ;;
    --name) NAME=$2; shift 2 ;;
    -h|--help) usage ;;
    -*) die "unknown option $1 (options go before the command)" ;;
    *) break ;;
  esac
done
[ $# -gt 0 ] || usage 2
cmd=$1; shift

abs() { mkdir -p -m 700 "$1" && (cd "$1" && pwd); }
is_loopback() { case $1 in 127.*|::1|localhost) return 0 ;; *) return 1 ;; esac; }

# the options every container gets: this user, read-only root, no network unless a job's port
BASE=(--rm --init --user "$(id -u):$(id -g)" --read-only --tmpfs /tmp:rw,exec,mode=1777
      -e HOME=/tmp --security-opt no-new-privileges)

job_opts() {  # JOB: a training container's caps, GPU, mounts and status port
  local data out
  data=$(cd "$DATA" 2>/dev/null && pwd) || die "--data $DATA: no such directory (run.sh unpack first)"
  [ -f "$data/manifest.json" ] || die "$data: not an unpacked bundle (no manifest.json)"
  out=$(abs "$OUT")
  JOB=(--name "$NAME" --cpus "$CPUS" --cpu-shares 512 --memory "$MEMORY" --memory-swap "$MEMORY"
       --shm-size "$SHM" --pids-limit 4096 -v "$data:/work/bundle:ro" -v "$out:/work/build/runs:rw")
  [ "$CPU_ONLY" = 1 ] || JOB+=(--gpus "$GPUS")
  if [ "$PORT" = 0 ]; then JOB+=(--network none); else JOB+=(-p "$BIND:$PORT:8686"); fi
}

job_args() {  # ARGS: the job runner's options, then -- and the kit's training options
  local dev=cuda port=8686
  [ "$CPU_ONLY" = 1 ] && dev=cpu
  [ "$PORT" = 0 ] && port=0
  ARGS=(--bundle /work/bundle --nice "$NICE" --linger "$LINGER" --status-port "$port"
        --status-bind 0.0.0.0 --status-reachable "$BIND"
        -- --device "$dev" --threads "$THREADS" --workers "$WORKERS")
}

warn_bind() {
  if [ "$PORT" != 0 ] && ! is_loopback "$BIND"; then
    echo "run.sh: WARNING: the status page is published on $BIND:$PORT, not loopback:" \
      "anyone who reaches it can read the run and download its checkpoints (no password)" >&2
  fi
}

running() { [ -n "$("$DOCKER" ps -q --filter "name=^${NAME}\$")" ]; }

case $cmd in
  build)
    DOCKER_BUILDKIT=1 "$DOCKER" build -f "$repo/train/gpu/Dockerfile" -t "$IMAGE" "$@" "$repo" ;;

  unpack)
    [ $# -eq 1 ] || die "unpack BUNDLE.tar"
    tar=$1
    [ -f "$tar" ] || die "$tar: no such file"
    if [ -f "$tar.sha256" ]; then
      (cd "$(dirname "$tar")" && sha256sum -c "$(basename "$tar").sha256") || die "$tar: sha256 differs"
    else
      echo "run.sh: no $tar.sha256 beside it: only the per-file sums are checked" >&2
    fi
    if [ -e "$DATA" ] && [ -n "$(ls -A "$DATA")" ]; then
      die "$DATA is not empty (run.sh clean, or --data another directory)"
    fi
    data=$(abs "$DATA")
    chmod 700 "$data"
    tdir=$(cd "$(dirname "$tar")" && pwd)
    "$DOCKER" run "${BASE[@]}" --network none -v "$tdir:/in:ro" -v "$data:/data:rw" --entrypoint python \
      "$IMAGE" -m glassine_train.bundle unpack "/in/$(basename "$tar")" /data ;;

  train|bench)
    [ "${1:-}" = -- ] && shift
    running && die "a job named $NAME is running (run.sh stop, or --name another)"
    [ "$cmd" = bench ] && LINGER=0
    warn_bind
    job_opts
    job_args
    extra=()
    [ "$cmd" = bench ] && extra=(--bench 10 --no-export)
    "$DOCKER" run -d "${BASE[@]}" "${JOB[@]}" "$IMAGE" train "${extra[@]}" "${ARGS[@]}" "$@" >/dev/null
    echo "run.sh: started $NAME; output: run.sh logs; stop: run.sh stop"
    [ "$PORT" = 0 ] || echo "run.sh: status page: http://$BIND:$PORT/ (from another machine: ssh -L $PORT:localhost:$PORT SERVER)"
    if [ "$cmd" = bench ]; then
      "$DOCKER" logs -f "$NAME" 2>&1 | grep --line-buffered -E '^(step |BENCH|--device|Traceback|[A-Za-z.]*(Error|Exit))' || true
    fi ;;

  selftest)
    dev=cuda gpu=(--gpus "$GPUS")
    if [ "$CPU_ONLY" = 1 ]; then dev=cpu gpu=(); fi
    "$DOCKER" run "${BASE[@]}" --network none --cpus "$CPUS" --memory "$MEMORY" --shm-size "$SHM" "${gpu[@]}" \
      "$IMAGE" selftest --device "$dev" ;;

  logs) exec "$DOCKER" logs -f "$NAME" ;;
  stop) exec "$DOCKER" stop "$NAME" ;;
  ps) exec "$DOCKER" ps --filter "name=^${NAME}\$" ;;

  serve)
    [ $# -eq 1 ] || die "serve RUN"
    out=$(cd "$OUT" 2>/dev/null && pwd) || die "--out $OUT: no such directory"
    [ -d "$out/$1" ] || die "$out/$1: no such run"
    warn_bind
    echo "run.sh: http://$BIND:$PORT/ (ctrl-c ends it)"
    exec "$DOCKER" run "${BASE[@]}" --name "$NAME-status" -v "$out:/work/build/runs:ro" -p "$BIND:$PORT:8686" \
      --entrypoint python "$IMAGE" -m glassine_train.status "/work/build/runs/$1" --bind 0.0.0.0 \
      --reachable-on "$BIND" ;;

  clean)
    bundle=
    if [ "${1:-}" = --bundle ]; then bundle=${2:?--bundle BUNDLE.tar}; fi
    running && die "a job named $NAME is running (run.sh stop first)"
    if [ -e "$DATA" ]; then
      data=$(cd "$DATA" && pwd)
      [ "$data" != / ] && [ "$data" != "$HOME" ] || die "refusing to delete $data"
      # only a directory unpack made: its manifest carries the bundle format
      grep -q '"format": "glassine-train-bundle 1"' "$data/manifest.json" 2>/dev/null \
        || die "$data: not an unpacked bundle, not deleted"
      rm -rf -- "$data"
      echo "run.sh: deleted $data"
    fi
    if [ -n "$bundle" ]; then
      case $bundle in *.tar) ;; *) die "--bundle $bundle: not a .tar" ;; esac
      rm -f -- "$bundle" "$bundle.sha256"
      echo "run.sh: deleted $bundle"
    fi
    if [ -d "$OUT" ]; then  # the resume state; best.pt, the export and the logs stay
      find "$OUT" -mindepth 2 -maxdepth 2 -type f \( -name last.pt -o -name last.pt.tmp \) -print -delete
    fi ;;

  *) usage 2 ;;
esac
