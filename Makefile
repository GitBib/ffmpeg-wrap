FILE_URL := https://github.com/GitBib/pymkv-files/raw/master/file.mkv
FILE_TWO_URL := https://github.com/GitBib/pymkv-files/raw/master/file_2.mkv

TEST_FILE := tests/file.mkv
TEST_TWO_FILE := tests/file_2.mkv

TEST_DIR := tests/

.PHONY: test download clean

# Download real media (if missing) then run the full suite, including the
# integration tests that shell out to ffmpeg.
test: download
	@echo "ffmpeg version:"; ffmpeg -version | head -n1
	uv run pytest $(TEST_DIR)

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
	rm -f $(TEST_FILE) $(TEST_TWO_FILE)
