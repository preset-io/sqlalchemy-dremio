// Fork publisher, following the same immutable-wheel pattern as the
// organization's other driver forks (sqlalchemy-drill, kylinpy,
// sqlalchemy-vertica-python). The version is declared once, in
// sqlalchemy_dremio/__init__.py. PR wheels carry a PEP 440 local version
// (<version>+pr.<number>.<revision>); stable wheels are published only from
// reviewed master. Plain branch builds test and build but never publish. An
// existing artifact is never overwritten (ci/publish_wheel.py).
podTemplate(
    imagePullSecrets: ['preset-pull'],
    containers: [
        containerTemplate(name: 'ci', image: 'preset/ci:latest',
            ttyEnabled: true, command: 'cat'),
        containerTemplate(name: 'py-ci', image: 'preset/python:3.9.18-2024-02-21-ci',
            ttyEnabled: true, command: 'cat')
    ]
) {
    node(POD_LABEL) {
        checkout scm
        def revision = sh(script: 'git rev-parse HEAD', returnStdout: true).trim()
        def baseVersion = sh(script: "sed -n \"s/^__version__ = '\\(.*\\)'/\\1/p\" sqlalchemy_dremio/__init__.py",
            returnStdout: true).trim()
        boolean isMaster = env.BRANCH_NAME == 'master'
        boolean isPR = env.CHANGE_ID != null
        boolean publish = isMaster || isPR
        def requested = isMaster ? baseVersion
            : isPR ? "${baseVersion}+pr.${env.CHANGE_ID}.${revision.take(12)}"
            : "${baseVersion}+branch.${revision.take(12)}"
        def version = ''
        def wheel = ''
        def key = ''

        container('py-ci') {
            stage('Resolve version') {
                // Normalize exactly as the build backend will (a numeric local
                // segment loses leading zeros), so the wheel filename, its
                // metadata and the published key always agree.
                withEnv(["REQUESTED_VERSION=${requested}"]) {
                    version = sh(script: '''
                        set -eu
                        python -m pip install --quiet 'packaging==24.2'
                        python -c 'import os; from packaging.version import Version; print(Version(os.environ["REQUESTED_VERSION"]))'
                    ''', returnStdout: true).trim()
                }
                if (!isMaster && !version.contains('+')) {
                    error("Non-master build produced a non-local version ${version}; refusing.")
                }
                wheel = "sqlalchemy_dremio-${version}-py3-none-any.whl"
                // Keep local-version PR wheels out of the stable package prefix.
                key = isPR ? "pr/sqlalchemy-dremio/${env.CHANGE_ID}/${wheel}"
                    : "sqlalchemy-dremio/${wheel}"
            }
            stage('Test and build') {
                withEnv(["PUBLISH_VERSION=${version}", "WHEEL=${wheel}"]) {
                    sh '''
                        set -eu
                        python -m venv .venv
                        .venv/bin/pip install 'sqlalchemy==2.0.52' 'pyarrow==17.0.0' 'pytest==8.3.5' \
                            'boto3>=1.36,<2' 'build==1.4.4' 'setuptools==80.9.0' 'wheel==0.45.1'
                        .venv/bin/pip install --no-deps .
                        # Server-free regressions; test/test_dremio.py needs a live Dremio.
                        for sa_version in 1.4.54 2.0.52; do
                            .venv/bin/pip install "sqlalchemy==$sa_version"
                            .venv/bin/python -m pytest -q -W error::DeprecationWarning -p no:cacheprovider test/test_sqlalchemy2.py \
                                test/test_additional.py test/test_flight_dialect.py test/test_publish_wheel.py
                        done
                        .venv/bin/python - <<'PY'
import os
from pathlib import Path
path = Path('sqlalchemy_dremio/__init__.py')
lines = path.read_text().splitlines(keepends=True)
assert sum(line.startswith('__version__ = ') for line in lines) == 1
path.write_text(''.join('__version__ = ' + repr(os.environ['PUBLISH_VERSION']) + '\\n'
                        if line.startswith('__version__ = ') else line for line in lines))
PY
                        SOURCE_DATE_EPOCH=$(git -c safe.directory="$PWD" log -1 --format=%ct)
                        case "$SOURCE_DATE_EPOCH" in
                            ''|*[!0-9]*) echo "Invalid commit timestamp for reproducible build" >&2; exit 1 ;;
                        esac
                        export SOURCE_DATE_EPOCH
                        # Pin the build backend and remove stale output for reproducible retries.
                        rm -rf build dist sqlalchemy_dremio.egg-info
                        .venv/bin/python -m build --wheel --no-isolation
                        test -f "dist/$WHEEL" || { echo "missing dist/$WHEEL"; ls -1 dist; exit 1; }
                        test "$(ls -1 dist | wc -l)" -eq 1 || { echo "unexpected dist contents"; ls -1 dist; exit 1; }
                        if .venv/bin/python -m zipfile -l "dist/$WHEEL" | awk '{print $1}' | grep -Eq '^(test|ci)/'; then
                            echo "wheel must ship only the sqlalchemy_dremio package"; exit 1
                        fi
                        .venv/bin/pip install --force-reinstall --no-deps "dist/$WHEEL"
                        .venv/bin/python - <<'PY'
import importlib.metadata as im
import os
import sqlalchemy as sa
assert im.version('sqlalchemy-dremio') == os.environ['PUBLISH_VERSION']
engine = sa.create_engine('dremio+flight://user:pass@localhost:32010/?UseEncryption=false')
assert engine.dialect.name == 'dremio+flight'
engine.dispose()
PY
                    '''
                }
            }
        }
        if (!publish) {
            echo 'Branch build verified; only master and pull-request builds publish.'
            archiveArtifacts artifacts: 'dist/*.whl', fingerprint: true
            return
        }
        container('ci') {
            stage('Publish immutable wheel') {
                withCredentials([[
                    $class: 'AmazonWebServicesCredentialsBinding',
                    credentialsId: 'ci-user',
                    accessKeyVariable: 'AWS_ACCESS_KEY_ID',
                    secretKeyVariable: 'AWS_SECRET_ACCESS_KEY'
                ]]) {
                    withEnv(["WHEEL=${wheel}", "KEY=${key}",
                             "ALLOW_IDENTICAL_PR_ARTIFACT=${isPR && !isMaster}"]) {
                        sh '''
                            set -eu
                            python -m pip install --quiet 'boto3>=1.36,<2'
                            python ci/publish_wheel.py
                        '''
                    }
                }
            }
        }
        archiveArtifacts artifacts: 'dist/*.whl,published.sha256', fingerprint: true
    }
}
