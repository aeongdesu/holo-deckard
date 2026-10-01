# syntax=docker/dockerfile:1

FROM scratch AS base
ADD build/base.tar.zst /
RUN ldconfig
ENV LANG=C.UTF-8
CMD ["/usr/bin/bash"]

FROM base AS base-devel
ADD build/base-devel.tar.zst /
RUN ldconfig

FROM base-devel AS full
ADD build/full.tar.zst /
RUN ldconfig
