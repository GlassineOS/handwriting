# syntax=docker/dockerfile:1
# glassine-train on an NVIDIA GPU (README.md here). Built from the repository root:
#   docker build -f train/gpu/Dockerfile -t glassine-train-gpu .
# Only the code is copied in (Dockerfile.dockerignore); data comes in at run time, read-only.

# PyTorch 2.15.0, CUDA 13.2, cuDNN 9, Python 3.12, linux/amd64; pinned by digest.
ARG BASE=pytorch/pytorch:2.15.0-cuda13.2-cudnn9-runtime@sha256:924707fa828f821c9a82d987b405675df6369eee5ce368aece6757b025ffcde5
FROM ${BASE}
# cuda: the base must carry a CUDA build of torch 2.15.0 (cpu: a test base without one)
ARG TORCH_CHECK=cuda
# the base is Ubuntu with its system Python 3.12 (externally managed): this image is that
# environment, so pip may install into it
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore PIP_BREAK_SYSTEM_PACKAGES=1 \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
COPY train/gpu/requirements.txt /src/train/gpu/requirements.txt
RUN python3 -m pip install -r /src/train/gpu/requirements.txt
COPY synth /src/synth
COPY train /src/train
# pip, not uv: the uv sources in pyproject.toml would swap in a CPU torch
RUN python3 -m pip install --no-deps /src/synth /src/train \
 && python3 -c "import torch, sys; v = torch.__version__; c = torch.version.cuda; print('torch', v, 'cuda', c); \
sys.exit(0 if v.startswith('2.15.0') and (c or '$TORCH_CHECK' == 'cpu') else 'torch is not the pinned CUDA build')" \
 && python3 -c "import glassine_train.cli, glassine_train.job, glassine_train.status, glassine_train.bundle"
# build/data and build/init are the bundle's (mounted read-only at /work/bundle),
# build/runs is OUT (mounted read-write): the same layout as a run on the CPU host
WORKDIR /work
RUN mkdir -p /work/build/runs /work/bundle \
 && ln -s ../bundle/data /work/build/data && ln -s ../bundle/init /work/build/init \
 && chmod -R a+rX /work
ENTRYPOINT ["python3", "-m", "glassine_train.job"]
