#!/usr/bin/env python3

"""Take a list of DRUIDs, and return a list of image URLs."""

import argparse
import asyncio
import logging
import re
from pathlib import Path

import aiofiles
import aiohttp
from aiohttp import ClientSession


async def write_image_urls_for_druid(
    output_path: Path, druid: str, session: ClientSession
):
    manifest = await get_manifest_for_druid(druid, session)

    assert manifest is not None, f"Could not get manifest for {druid}"

    image_data = get_image_data_from_manifest(druid, manifest)

    logging.info(f"Writing data for {druid} ({len(image_data)} images)")
    async with aiofiles.open(output_path, "a") as _fh:
        for url, filename in image_data:
            await _fh.write(f"{url}\t{filename}\n")


async def get_manifest_for_druid(druid: str, session: ClientSession) -> dict | None:
    """Get the manifest for a given DRUID."""
    try:
        async with session.get(
            f"https://purl.stanford.edu/{druid}/iiif/manifest"
        ) as response:
            if response.status == 200:
                return await response.json()
            else:
                logging.fatal("Request failed: %s", response)
    except aiohttp.ClientConnectorError as err:
        logging.fatal("Connection error: %s", str(err))

    return None


def get_image_data_from_manifest(druid: str, manifest: dict) -> list[tuple[str, str]]:
    """
    Get the image URLs from the manifest.
    This function is littered with assertions that make explicit the assumptions I made
     about the manifest when writing the function -- if those assumptions don't hold,
     the assertions will fail rather than the script continuing with undetermined results.
    """

    # expect only one sequence
    assert len(manifest["sequences"]) == 1

    image_urls = [
        image["resource"]["@id"]
        for canvas in manifest["sequences"][0]["canvases"]
        for image in canvas["images"]
    ]

    # expect the same number of image URLs as the number of canvases
    #  (i.e. one image per canvas)
    assert len(image_urls) == len(manifest["sequences"][0]["canvases"])

    pattern = rf"https://stacks.stanford.edu/image/iiif/{druid}/([^/]+)/full/full/0/default.jpg"
    image_data = []
    for url in image_urls:
        # check that all URLs match the expected pattern
        # the general form should be .../{druid}/{druid}_{xxxx}/...
        #  * unfortunately, the numeric suffixes are not always sequential
        #    e.g. cp084fk4131 goes from cp084fk4131_0012 to cp084fk4131_0014
        #  * the suffixes may also be preceded by an underscore on not
        #    e.g. ds477mv6523 has URLs of the form .../ds477mv6523/ds477mv65230001/... etc.
        #  * there are also some where the part with the suffix has a broken druid
        #    e.g. gv471qh7025 (the URLs are .../gv471qh7025/gv471qh702_0001/... -- note the missing '5')

        # the last one is really the kicker -- it's only one druid, but it means that the
        #  pattern must be relaxed considerably.  Ah well, it is what it is...

        assert (
            match := re.match(pattern, url)
        ), f"'{url}' does not match expected pattern"

        image_data.append((url, f"{druid}/{match.group(1)}.json"))

    return image_data


async def main():
    """Command-line entry-point."""

    parser = argparse.ArgumentParser(description="Description: {}".format(__doc__))
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Show verbose output",
    )
    parser.add_argument(
        "-o",
        "--output",
        action="store",
        default=None,
        help="Path to output file (defaults to image_urls.tsv)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Overwrite output file if it already exists",
    )
    parser.add_argument(
        "input", help="Path to a file containing a list of DRUIDs (one per line)"
    )

    args = parser.parse_args()
    input_path = Path(args.input)
    assert input_path.exists(), "Input file does not exist"

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    with input_path.open("r") as _fh:
        druids = [line.strip() for line in _fh]

    if args.output is not None:
        output_path = Path(args.output)
    else:
        output_path = Path("image_urls.tsv")

    if output_path.exists():
        if args.overwrite:
            output_path.unlink()
        else:
            logging.fatal(
                "Output file %s already exists; pass --overwrite to overwrite",
                output_path,
            )
            raise SystemExit(0)

    async with ClientSession() as session:
        await asyncio.gather(
            *(
                write_image_urls_for_druid(
                    output_path=output_path, druid=druid, session=session
                )
                for druid in druids
            )
        )


if __name__ == "__main__":
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())
