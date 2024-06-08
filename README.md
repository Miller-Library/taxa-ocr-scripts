# taxa-scripts
![Static Badge](https://img.shields.io/badge/python-%E2%89%A53.10-blue?logo=python&logoColor=white)

This repository contains a couple of scripts for taking a list of druids and performing OCR on the associated image files using the [Transkribus Metagrapho API](https://www.transkribus.org/metagrapho/documentation).

`druid_to_image_list.py` takes a list of druids and writes out a file suitable as input for `recognize_with_transkribus.py`.

The results of Taxa Project druids processed with these scripts can be found at https://github.com/Miller-Library/taxa-ocr-output.


## `druid_to_image_list.py`

```sh
% ./druid_to_image_list.py --help
usage: druid_to_image_list.py [-h] [-v] [-o OUTPUT] [--overwrite] input

Description: Take a list of DRUIDs, and return a list of image URLs.

positional arguments:
  input                 Path to a file containing a list of DRUIDs (one per line)

options:
  -h, --help            show this help message and exit
  -v, --verbose         Show verbose output
  -o OUTPUT, --output OUTPUT
                        Path to output file (defaults to image_urls.tsv)
  --overwrite           Overwrite output file if it already exists
```

DRUIDs are use to construct IIIF manifest URLs.  These manifests are fetched, and parsed to generate a list of IIIF Image API URLs for images of individual pages.  Script has been validated to work with the DRUIDs needed for the Taxa Project; compatibility with every possible IIIF manifest not guaranteed 🙂

Output is two fields, one record per line, no headers, tab separated.


**Non-stdlib Requirements:**
* `aiofiles`
* `aiohttp`


## `recognize_with_transkribus.py`

```sh
% ./recognize_with_transkribus.py --help
usage: recognize_with_transkribus.py [-h] [-v] [--output-folder OUTPUT_FOLDER] [--concurrency CONCURRENCY] [--limit LIMIT] image-tasks

Description: Simple Transkribus API client

positional arguments:
  image-tasks           Path to a file of the form: <URL or file path to image> <filename/for/response.json> (one record per line)

options:
  -h, --help            show this help message and exit
  -v, --verbose         Show additional output
  --output-folder OUTPUT_FOLDER
                        Folder to output JSON responses (default: ./output)
  --concurrency CONCURRENCY
                        Maximum number of concurrent image recognition tasks (default: 50)
  --limit LIMIT         Maximum number of tasks to process from the input file (includes skipped/failed tasks) (default: all)
```

This script is a client for the Transkribus Metagrapho API.  The input is pairs of a source image (either a path to a local image file or a URL to a publicly-accessible image) and an destination path to write the API response (including the recognized text) to (the destination will be relative to `OUTPUT_FOLDER`).  Two fields, one record per line, no headers, whitespace (any) separated, e.g.

<pre>
https://stacks.stanford.edu/image/iiif/bm466nw8277/bm466nw8277_0001/full/full/0/default.jpg   bm466nw8277/bm466nw8277_0001.json
https://example.com/collection1/image_a.jpg   collection1/image_a.ocr.json
./images/image01.jpeg                         output01.json
</pre>

Transkribus API credentials must be supplied as `TRANSKRIBUS_USER` and `TRANSKRIBUS_PASSWORD` environment variables, optionally (recommended) in a `.env` file, e.g.:

```env
TRANSKRIBUS_USER=transkribus-user@example.com
TRANSKRIBUS_PASSWORD=super-secret-p@55w0rd
```

**Non-stdlib Requirements:**
* `aiohttp`
* `dotenv`
* `rich`


### How it works

1. An access token is requested from the OIDC API using the supplied credentials
2. A number of processing requests are submitted to the Transkribus Metagrapho API equal to the value of `--concurrency` (default: 50).
3. If the jobs are accepted, the API is polled at intervals of 5 seconds until the job is complete and the result is returned.
4. Successful results are written to the specified output file, as determined by `--output-folder` and the value supplied in `image-tasks`.
5. As tasks are completed, new tasks are submitted to keep the number of running tasks at `--concurrency`.
6. When all tasks are completed, the script exits, reporting the number of tasks completed, skipped, and failed.

Failed jobs are not retried; however, output is only written if the job is successful and jobs for which an output file already exists will be skipped, so for intermittent API failures or connection issues it is sufficient to re-run the script with the same arguments; completed jobs will be skipped, and only unprocessed jobs will be posted.

The script will automatically refresh the access token before it times out, and TCP connections will be re-established before the API server times them out (anecdotal evidence suggests the Metagrapho API times out http `keep-alive` connections after ~10 seconds).  It will also automatically "logout" and revoke the tokens when the operation is complete.

If the supplied credentials run out of credits during the operation this will be reported, and subsequent image tasks will not be attempted.


### Limitations

* The API endpoints for Metagrapho and the OIDC server are hard-coded at the top of the script (although this is probably appropriate).
* The model ID for the "Print 0.3" model is hard-coded at the top of the script, and should be parameterized.
* The script has become sufficiently complicated that it should be refactored to a class-based client and a separate (optional) CLI before using it for another project.
* This has only been battle-tested with this one project; although it should be, at the least, a very strong start for something general-purpose, care should be taken if attempting to use this in another context.
