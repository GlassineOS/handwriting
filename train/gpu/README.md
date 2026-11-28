# Training on a GPU server

A container kit for `glassine-train train` on an NVIDIA GPU: the image holds only the code
(no data), the run's inputs come in as one **bundle** made on the machine that has the
data, and the run lands in an output directory with the same layout as `build/runs/RUN` on
the CPU host, so `eval`, `export` and `release` go on there as usual. A small read-only status
page shows the run while it trains.

The image: `pytorch/pytorch:2.15.0-cuda13.2-cudnn9-runtime`, pinned by digest (`Dockerfile`),
plus the pinned packages of `requirements.txt`. The kit runs the job niced, with CPU and memory
caps, so the server keeps room for its other work.

## What the server needs

- An NVIDIA driver of branch **580 or newer** (CUDA 13; 595 or newer matches CUDA 13.2 exactly).
  `nvidia-smi` shows it. Arch: `nvidia-open` (or `nvidia`) and `nvidia-utils`.
- Docker: Arch `docker`, the service running, the training user in the `docker` group.
- The NVIDIA Container Toolkit, so containers see the GPU. Arch: `nvidia-container-toolkit`,
  then:

  ```sh
  sudo pacman -S --needed nvidia-container-toolkit
  sudo nvidia-ctk runtime configure --runtime=docker
  sudo systemctl restart docker
  docker run --rm --gpus all pytorch/pytorch:2.15.0-cuda13.2-cudnn9-runtime nvidia-smi   # the GPU shows
  ```

  Without the toolkit `--gpus all` fails (for instance `failed to discover GPU vendor from
  CDI`).
- Disk for the image (the base is about 3 GB to download and several times that unpacked;
  `docker images` shows it after the build), plus the bundle twice (the tarball and its
  unpacked copy) and the runs.

## First: the kit on the server, without data

```sh
git clone https://github.com/GlassineOS/handwriting && cd handwriting/train/gpu
./run.sh build          # the image, from this checkout
./run.sh selftest       # ~1 min, synthetic ink: 40 steps on the GPU, the checkpoint read
                        # back with the GPU hidden, ONNX export; ends in "selftest: PASS"
```

Only when that passes does the data need to move. If the GPU setup cannot work with a
read-only container root, `./run.sh --writable-root selftest` (and the same option for the
other commands) leaves the root writable.

## The data: a bundle, never in the repository

The kit never contains or downloads data. On the machine with the data, in `train/`, make a
bundle from the same `train` options the run takes:

```sh
uv run glassine-train bundle --out ~/m5.tar -- --run m5 --data train5 --val val5.xh \
    --select dev5.xh,dev5_kinds.xh,brush5-dev --augment --hidden 176 --minutes 600 --steps-per-s RATE
```

It packs exactly the featurized datasets of `--data`, `--val` and `--select` (and the
checkpoint of `--init`, if given) with a manifest: the training arguments rewritten to the
bundle's layout, each file's size and sha256. `m5.tar.sha256` beside it is the tarball's
sum. Both are written readable only by you. `--resume` is not bundled: a stopped run goes on
where its `last.pt` is (below).

**The bundle may hold data used under non-commercial terms (BRUSH) or a person's own
writing.** Keep it on machines that person controls, readable only by the training user, and
delete it, and its unpacked copy, after the run (`run.sh clean`). A model trained on it
carries the terms of its data. Copy it over SSH into a directory only you can read:

```sh
ssh SERVER 'mkdir -m 700 -p ~/glassine-train'
scp -p ~/m5.tar ~/m5.tar.sha256 SERVER:glassine-train/
```

## Training on the server

```sh
cd handwriting/train/gpu
./run.sh unpack ~/glassine-train/m5.tar          # checks the sums; into ~/glassine-train/data (0700)
./run.sh bench                                   # at most 10 min: 300 steps of the bundle's run, prints steps/s
./run.sh train                                   # the bundle's run, in the background
./run.sh logs                                    # follow it (ctrl-c leaves it running)
```

A job that fails at once (a wrong option, no GPU) is reported by `run.sh train` with its last
output; its container stays (no data in it) until the next `train` or `clean`, so `run.sh logs`
still shows what happened.

`run.sh train -- OPTIONS` adds `glassine-train train` options after the bundle's (a later
option wins), e.g. `./run.sh train -- --steps-per-s 9 --minutes 120`. The kit itself adds
`--device cuda --threads 4 --workers 4` (before yours). Kit options go before the command:

| option | default | |
|---|---|---|
| `--data DIR` | `~/glassine-train/data` | the unpacked bundle |
| `--out DIR` | `~/glassine-train/out` | the runs: `OUT/RUN/` |
| `--cpus N` | 8 | CPU cap (`docker --cpus`), and a lower CPU weight than other containers |
| `--memory SIZE` | 16g | memory cap, no swap beyond it |
| `--shm SIZE` | 2g | `/dev/shm` for the data loader processes |
| `--nice N` | 10 | niceness of the training |
| `--threads N`, `--workers N` | 4, 4 | torch CPU threads, data loader processes |
| `--gpus SPEC` | all | `docker --gpus`, e.g. `device=0` |
| `--status-port P` | 8686 | the status page (0: none) |
| `--status-bind A` | 127.0.0.1 | the host address the page is published on |
| `--linger S` | 86400 | seconds the page stays up after the job, for the download |
| `--name NAME` | glassine-train | container name: one job per name |
| `--cpu-only` | | no GPU, `--device cpu`: the kit on any machine |
| `--writable-root` | | no read-only container root |

**Benchmark.** `./run.sh bench` trains the bundle's run for 300 steps (the schedule set
for 300, evaluation only at the end) and prints `BENCH ... steps/s`. The same on the CPU host,
in `train/`, with the run's options:

```sh
uv run glassine-train train --run bench-cpu OPTIONS --minutes 10 --steps-per-s 0.5 --eval-every 1000000000
```

The trainer prints its mean steps/s every 100 steps. A small model at batch 32 leaves much
of the GPU idle; a larger `--batch` (with the learning rate scaled) uses more of it but is a
different recipe, so compare runs at the same batch first.

**The schedule.** `--minutes` is a hard deadline and `--minutes × 60 × --steps-per-s` the
step count, as on the CPU (`../README.md`). For the same 16000 steps as a CPU run, keep their
product and give the measured GPU rate, e.g. `-- --steps-per-s 9 --minutes 30`, with margin.

**Stop and resume.** `./run.sh stop` ends the job; `OUT/RUN/last.pt` (written at every
evaluation) holds what a resumed run needs. `./run.sh train -- --resume` goes on with it. A GPU
run is not bit-reproducible (cuDNN's LSTM and the CTC loss on CUDA), and a resumed GPU run is
close to, not equal to, one never stopped; a CPU run stays exact.

## The status page

While the job runs (and `--linger` seconds after), `http://127.0.0.1:8686/` on the server
shows the run: step / total, loss and dev CER over time, learning rate, steps/s, ETA, the
GPU's use and memory (from `nvidia-smi`), the last log lines and download links for
`best.pt`, `last.pt` and, after training, the exported `recognizer.onnx`. From your machine:

```sh
ssh -N -L 8686:localhost:8686 SERVER      # then open http://localhost:8686/
```

It is read-only, serves only those named files of the one run (no listing, nothing else in
OUT) and has no password, so it is published on 127.0.0.1 only. `--status-bind 0.0.0.0`
publishes it to the network: anyone who reaches the port can read the run and download its
model, so only on a network you trust (the kit warns). `./run.sh serve RUN` shows a finished
run's page again.

## Where the output lands

`OUT/RUN/` (default `~/glassine-train/out/RUN/`), files readable only by you:
`best.pt` (lowest dev CER), `last.pt` (resume state), `log.jsonl` (each evaluation),
`train.log` (the whole output), `args.json` (the options, as on the CPU), `job.json` (the
kit's state), and after training `recognizer.onnx`, `recognizer-int8.onnx` and `export.json`
(`glassine-train export`, run on the CPU, checked on the run's `--val` set). Checkpoints hold
CPU tensors. Take the run home and go on there:

```sh
scp -rp SERVER:glassine-train/out/m5 build/runs/       # on the CPU host, in train/
uv run glassine-train eval --run m5 ...                 # tune, export, release as in ../README.md
```

## Clean up

```sh
./run.sh stop                                    # the job, or its page still lingering
./run.sh clean --bundle ~/glassine-train/m5.tar
```

deletes the unpacked bundle (only a directory `unpack` made), the tarball and its `.sha256`,
and every run's `last.pt` (the resume state). `best.pt`, the export and the logs stay.
Deleting on an SSD does not overwrite the blocks; for data that must not stay readable, keep
the training directory on an encrypted disk. Delete the bundle on the data machine too once
it is no longer needed. The image holds no data (`docker image rm glassine-train-gpu`
removes it).
