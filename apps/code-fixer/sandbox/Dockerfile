# The sandbox every test run happens in: Python 3.12 and pytest, nothing else.
# Build once (the build needs the network; test runs never get it):
#   docker build -t code-fixer-sandbox:1 sandbox
FROM python:3.12-slim

RUN pip install --no-cache-dir "pytest==8.3.5" \
 && useradd --create-home --uid 1000 sandbox

COPY run-tests.sh /usr/local/bin/run-tests
RUN chmod 755 /usr/local/bin/run-tests

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONHASHSEED=0 HOME=/tmp
USER sandbox
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/run-tests"]
