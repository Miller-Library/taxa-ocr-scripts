#!/usr/bin/env python3

"""Simple Transkribus API client"""

import argparse
import asyncio
import base64
import json
import logging
import os
from pathlib import Path
from typing import Coroutine
from urllib.parse import urlparse

import aiohttp
from aiohttp import ClientSession
from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler

OIDC_ENDPOINT = (
    "https://account.readcoop.eu/auth/realms/readcoop/protocol/openid-connect"
)

PROCESSES_ENDPOINT = "https://transkribus.eu/processing/v1/processes"

HTRID = 36202  # Model ID for "Print 0.3"


def validate_url(url: str) -> bool:
    """Validate that a URL can be successfully parsed."""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False


async def get_api_token(username: str, password: str, session: ClientSession) -> dict:
    """Retrieve an API token from the Transkribus OIDC API."""

    data = {
        "grant_type": "password",
        "username": username,
        "password": password,
        "client_id": "processing-api-client",
    }
    async with session.post(OIDC_ENDPOINT + "/token", data=data) as response:
        return await response.json()


async def refresh_api_token(session: ClientSession) -> dict:
    """Refresh the existing API token."""

    data = {
        "grant_type": "refresh_token",
        "client_id": "processing-api-client",
        "refresh_token": token["refresh_token"],
    }
    async with session.post(OIDC_ENDPOINT + "/token", data=data) as response:
        return await response.json()


async def revoke_api_token(refresh_token: str, session: ClientSession) -> None:
    """Revoke the existing API token and invalidate the refresh token."""

    data = {
        "refresh_token": refresh_token,
        "client_id": "processing-api-client",
    }

    try:
        async with session.post(OIDC_ENDPOINT + "/logout", data=data) as response:
            if response.status != 204:
                logging.fatal("Token revocation request failed: %s", response)
            return
    except aiohttp.ClientConnectorError as err:
        logging.fatal("Connection error: %s", str(err))


def get_image_as_base64(image_path: Path) -> str:
    """Get the image file at the given `image_path` and encode it as a base64 string."""
    with image_path.open("rb") as _fh:
        encoded_string = base64.b64encode(_fh.read())

    return encoded_string.decode("utf-8")


async def submit_image_for_processing(
    image: Path | str, session: ClientSession
) -> str | None:
    """Submit an image to the Transkribus API for processing."""
    global no_credits
    request_body = {
        "config": {"textRecognition": {"htrId": HTRID}},
        "image": {},
    }

    if isinstance(image, str) and image.startswith("http"):
        assert validate_url(image), f"'{image}' is not a valid URL"
        request_body["image"]["imageUrl"] = image
    else:
        image = Path(image)
        assert image.exists(), f"Image file '{image}' does not exist"
        request_body["image"]["base64"] = get_image_as_base64(image)

    headers = {"Authorization": f"Bearer {token['access_token']}"}

    try:
        async with session.post(
            PROCESSES_ENDPOINT,
            json=request_body,
            headers=headers,
        ) as response:
            if response.status == 200:
                return (await response.json())["processId"]
            elif response.status == 429:
                logging.error("Image submission request failed: no more credits!")
                no_credits = True
            else:
                logging.fatal("Image submission request failed: %s", response)
    except aiohttp.ClientConnectorError as err:
        logging.fatal("Connection error: %s", str(err))


async def check_processing_status(
    process_id: str, session: ClientSession
) -> dict | None:
    """Check the processing status of a given process ID using the Transkribus API."""
    headers = {"Authorization": f"Bearer {token['access_token']}"}

    try:
        async with session.get(
            f"{PROCESSES_ENDPOINT}/{process_id}", headers=headers
        ) as response:
            return await response.json()
    except aiohttp.ClientConnectorError as err:
        logging.fatal("Connection error: %s", str(err))


def write_output(status_response: dict, output_path: Path) -> None:
    """Write the output from the processing status response to the given `output_path`."""
    with output_path.open("w") as _fh:
        json.dump(status_response, _fh, indent=2)


async def process_image(
    image: Path | str,
    output_path: Path | str,
    session: ClientSession,
) -> None:
    """
    Submit an image for processing, wait for the processing to complete, and write the
    response to the given `output_path`.
    """

    if no_credits:
        logging.debug(f"Not submitting {image}\n-- no credits left!")
        counts["failed"] += 1
        return

    output_path = Path(output_path)

    if output_path.exists():
        logging.debug("%s -- output file already exists: skipping", output_path)
        counts["skipped"] += 1
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)

    logging.info(f"Submitting {image} for processing...")
    process_id = await submit_image_for_processing(image, session)
    if process_id is None:
        logging.fatal(f"Failed to submit {image} for processing!")
        counts["failed"] += 1
        return
    logging.info(f"Successfully submitted {image} for processing (pid: {process_id})")

    status_response = await check_processing_status(process_id, session)
    await asyncio.sleep(5)
    while status_response is not None and status_response["status"] in [
        "CREATED",
        "WAITING",
        "RUNNING",
    ]:
        logging.debug(status_response)
        await asyncio.sleep(5)
        status_response = await check_processing_status(process_id, session)

    if status_response is not None:
        logging.info("Success -- writing output to %s", output_path)
        write_output(status_response, Path(output_path))
        counts["processed"] += 1
    else:
        logging.fatal("Processing failed? Image: %s Process ID: %s", image, process_id)
        counts["failed"] += 1


async def token_refresh_task(session: ClientSession):
    """Refresh the access token in the background when it is close to expiring."""
    global token
    refresh_period = int(token["expires_in"] * 0.9)
    while True:
        logging.debug(f"Refreshing access token in {refresh_period} seconds")
        await asyncio.sleep(refresh_period)
        logging.info("Refreshing access token...")
        token = await refresh_api_token(session)
        assert "access_token" in token, "Failed to refresh access token: " + json.dumps(
            token
        )


async def gather_with_concurrency(n: int, *coroutines: Coroutine) -> list:
    """Use asyncio.Semaphore to limit the number of concurrent coroutines to `n`."""
    semaphore = asyncio.Semaphore(n)

    async def sem_coroutine(coroutine):
        async with semaphore:
            return await coroutine

    return await asyncio.gather(*(sem_coroutine(c) for c in coroutines))


async def main():
    """Command-line entry-point."""

    parser = argparse.ArgumentParser(description=f"Description: {__doc__}")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Show additional output",
    )
    parser.add_argument(
        "--output-folder",
        action="store",
        default="output",
        help="Folder to output JSON responses (default: ./output)",
    )
    parser.add_argument(
        "--concurrency",
        action="store",
        type=int,
        default=50,
        help="Maximum number of concurrent image recognition tasks (default: 50)",
    )
    parser.add_argument(
        "--limit",
        action="store",
        type=int,
        default=None,
        help="Maximum number of tasks to process from the input file (includes skipped/failed tasks) (default: all)",
    )
    parser.add_argument(
        "image_tasks",
        metavar="image-tasks",
        help="""
        Path to a file of the form:
          <URL or file path to image> <filename/for/response.json>
        (one record per line)
        """,
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[RichHandler(markup=False, console=Console(width=180))],
    )

    load_dotenv()
    username = os.getenv("TRANSKRIBUS_USER")
    password = os.getenv("TRANSKRIBUS_PASSWORD")

    if not (username and password):
        logging.fatal("Please supply both TRANSKRIBUS_USER and TRANSKRIBUS_PASSWORD!")
        raise SystemExit(1)

    # the type conversion performed by argparse is sufficient to ensure the value is an int
    assert args.concurrency > 0, "Concurrency must be positive integer!"
    if args.limit is not None:
        assert args.limit > 0, "Limit must be positive integer!"

    if not Path(args.image_tasks).exists():
        logging.fatal("Input file '%s' does not exist!", args.image_tasks)
        raise SystemExit(1)

    output_folder = Path(args.output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)

    with open(args.image_tasks, "r") as _fh:
        image_tasks = [line.strip().split() for line in _fh if line.strip()]

    if args.limit is not None:
        image_tasks = image_tasks[: args.limit]

    global counts, no_credits
    counts = {
        "processed": 0,
        "failed": 0,
        "skipped": 0,
    }
    no_credits = False

    # The Transkribus API appears to limit the duration of a TCP connection to
    #  ~10 seconds.  Using a custom TCPConnector we can disable http keep-alive
    #  to use new connections where needed and prevent unexpected closures.
    connector = aiohttp.TCPConnector(force_close=True)
    async with ClientSession(connector=connector) as session:
        logging.info("Getting access token...")
        global token
        token = await get_api_token(username, password, session)
        assert "access_token" in token, "Failed to get access token: " + json.dumps(
            token
        )

        loop = asyncio.get_event_loop()
        loop.create_task(token_refresh_task(session))

        await gather_with_concurrency(
            args.concurrency,
            *(
                process_image(
                    image=image,
                    output_path=output_folder / output_path,
                    session=session,
                )
                for image, output_path in image_tasks
            ),
        )

        logging.info("Revoking API tokens...")
        await revoke_api_token(token["refresh_token"], session)

    logging.info("Operation complete!")
    logging.info(
        "Processed %d images, %d failed, %d skipped",
        counts["processed"],
        counts["failed"],
        counts["skipped"],
    )


if __name__ == "__main__":
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()

    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())
