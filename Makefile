FILE_URL := https://github.com/GitBib/pymkv-files/raw/master/file.mkv
FILE_TWO_URL := https://github.com/GitBib/pymkv-files/raw/master/file_2.mkv

TEST_FILE := tests/file.mkv
TEST_TWO_FILE := tests/file_2.mkv

TEST_DIR := tests/
PLATFORM_LOG := .pytest-platform.log

.PHONY: test test-platform download clean

# Download real media (if missing) then run the full suite, including the
# integration tests that shell out to ffmpeg.
test: download
	@echo "ffmpeg version:"; ffmpeg -version | head -n1
	uv run pytest $(TEST_DIR) -ra

test-platform:
	@rm -f $(PLATFORM_LOG) $(PLATFORM_LOG).status
	( uv run pytest tests/test_timeout.py tests/test_aio_timeout.py tests/test_platform_matrix.py -rs -p no:randomly 2>&1; \
		echo $$? > $(PLATFORM_LOG).status ) | tee $(PLATFORM_LOG)
	@status=$$(cat $(PLATFORM_LOG).status); rm -f $(PLATFORM_LOG).status; \
		test "$$status" -eq 0 || { echo "test-platform: pytest exited with status $$status"; exit 1; }
	@! grep -E '[0-9]+ skipped' $(PLATFORM_LOG) || { echo "test-platform: skipped timeout tests are not allowed"; exit 1; }

download: $(TEST_FILE) $(TEST_TWO_FILE)

$(TEST_FILE):
	@if [ ! -f $(TEST_FILE) ]; then \
		echo "Downloading $(TEST_FILE)..."; \
		curl -fsSL $(FILE_URL) -o $(TEST_FILE); \
		echo "Downloaded to $$(realpath $(TEST_FILE))"; \
	else \
		echo "$(TEST_FILE) already exists. Skipping download."; \
	fi

$(TEST_TWO_FILE):
	@if [ ! -f $(TEST_TWO_FILE) ]; then \
		echo "Downloading $(TEST_TWO_FILE)..."; \
		curl -fsSL $(FILE_TWO_URL) -o $(TEST_TWO_FILE); \
		echo "Downloaded to $$(realpath $(TEST_TWO_FILE))"; \
	else \
		echo "$(TEST_TWO_FILE) already exists. Skipping download."; \
	fi

clean:
	rm -f $(TEST_FILE) $(TEST_TWO_FILE) $(PLATFORM_LOG) $(PLATFORM_LOG).status
