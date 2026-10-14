FROM csbase.registry.cmbchina.cn/paas/cmb-nodejs-22.22:c86-kylin10-v1 AS node_runtime
USER root
ARG NPM_REGISTRY=http://central.jaf.cmbchina.cn/artifactory/api/npm/group-npm/
ARG LCR_VERSION=1.0.9
RUN mkdir -p /node-runtime/bin && cp -L "$(command -v node)" /node-runtime/bin/node \
    && cp -a "$(dirname "$(dirname "$(readlink -f "$(command -v npm)")")")" /node-runtime/npm
RUN npm install --prefix /lcr-runtime --registry="$NPM_REGISTRY" --ignore-scripts --no-audit --no-fund "@LV01.30/leancode-code-review-cli@$LCR_VERSION"
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --include=dev --registry="$NPM_REGISTRY" --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM csbase.registry.cmbchina.cn/paas/cmb-python-3.12:c86-kylin10-v1
USER root
WORKDIR /opt/deployments
ENV TZ=Asia/Shanghai PYTHONUTF8=1 PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    BENCH_PROJECT_ROOT=/opt/deployments PYTHONPATH=/opt/deployments/backend/src:/opt/deployments \
    BENCH_SERVER_MODE=1 BENCH_DATA_ROOT=/opt/deployments/runtime \
    BENCH_CONFIG_ROOT=/opt/deployments/runtime/models \
    BENCH_FRONTEND_DIST=/opt/deployments/frontend-dist \
    BENCH_STORAGE_MODE=persistent BENCH_RUN_STORAGE_MODE=cos BENCH_CASE_STORAGE_MODE=cos BENCH_ENABLED_SUITES=aacr BENCH_CASE_UPLOAD=1 \
    BENCH_AUTH_MODE=basic BENCH_CONTAINER_ROLE=all \
    HOME=/opt/deployments/runtime/home \
    NPM_CONFIG_REGISTRY=http://central.jaf.cmbchina.cn/artifactory/api/npm/group-npm/ \
    PATH=/opt/deployments/bin:/opt/deployments/.venv/bin:/usr/local/bin:/usr/bin:/bin
RUN ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && echo Asia/Shanghai > /etc/timezone \
    && yum install -y git procps-ng libstdc++ && yum clean all
COPY --from=node_runtime /node-runtime/bin/node /usr/local/bin/node
COPY --from=node_runtime /node-runtime/npm/ /usr/local/lib/node_modules/npm/
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY backend/requirements.txt backend/requirements.txt
RUN python3 -c "import sys; assert sys.version_info[:2] == (3, 12), sys.version" \
    && python3 -m venv .venv \
    && .venv/bin/python -m pip install --disable-pip-version-check --no-cache-dir --only-binary=:all: \
       --index-url http://central.jaf.cmbchina.cn/artifactory/api/pypi/group-pypi/simple \
       --trusted-host central.jaf.cmbchina.cn -r backend/requirements.txt \
    && .venv/bin/python -m pip freeze > /opt/deployments/requirements-container.resolved.txt
COPY . /opt/deployments/
COPY --from=node_runtime /lcr-runtime/node_modules/@LV01.30/leancode-code-review-cli/ /opt/deployments/lcr/
COPY --from=node_runtime /frontend/dist/ /opt/deployments/frontend-dist/
COPY deployment/lcr-linux /opt/deployments/bin/lcr
RUN rm -rf frontend && chmod 755 bin/lcr lcr/dist/lcr-linux-x64 \
    && git --version && node --version && npm --version && bin/lcr version \
    && .venv/bin/python -m deployment.container --check-build \
    && mkdir -p runtime/models runtime/home \
    && chmod -R 777 runtime
EXPOSE 8080
USER root
CMD ["/opt/deployments/.venv/bin/python", "-m", "deployment.container"]
