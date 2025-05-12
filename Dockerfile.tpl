# Use build arguments to specify the Neovim package name
ARG VERSION=0.10.4
ARG PKGNAME=nvim-linux-x86_64

#--| Local dev overrides |-----------------------------------------------------
FROM %%__BASE_IMAGE__%% AS override
USER root

RUN apt-get update && apt-get install -y \
    wget curl git netcat-traditional ripgrep fd-find rsync unzip xclip xsel \
    luarocks cmake python3-venv

RUN if ! command -v pip3; then apt-get install -y pip3; fi

#--| Base dev dependencies |----------------------------------------------------
FROM override AS dev-deps
WORKDIR /

ARG VERSION
ARG PKGNAME

# Install neoovim
RUN echo "VERSION: $VERSION, PKGNAME: $PKGNAME"
RUN wget https://github.com/neovim/neovim/releases/download/v$VERSION/$PKGNAME.tar.gz && \
    tar xzf $PKGNAME.tar.gz && \
    rsync -av $PKGNAME/ /usr/local/

# Install python vim dependencies
RUN pip3 install --isolated  --index-url https://pypi.org/simple/ pynvim

# Install requirements.local if it exists
RUN bash -c 'if [ -f requirements.local ]; then pip3 install --isolated -r requirements.local; fi'

# Install nodejs dev dependencies
RUN if ! command -v npm; then \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs; \
fi

RUN npm install --registry=https://registry.npmjs.org --verbose -g neovim typescript emmet-ls tree-sitter-cli

# --| Final |-------------------------------------------------------------------
# Set up any other needed dirs
FROM dev-deps AS nvim-devcontainer
RUN mkdir -p /nvim-devcontainer/config /nvim-devcontainer/data /nvim-devcontainer/cache

ENV XDG_CONFIG_HOME="/nvim-devcontainer/config"
ENV XDG_DATA_HOME="/nvim-devcontainer/data"
ENV XDG_CACHE_HOME="/nvim-devcontainer/cache"

ENV NVIM_DEVCONTAINER=1


# vim: tw=120 ft=dockerfile
