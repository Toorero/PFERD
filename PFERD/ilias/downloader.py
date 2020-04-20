"""Contains a downloader for ILIAS."""

from pathlib import Path
from typing import Any, Dict

import bs4
import requests

from ..new_organizer import Organizer
from ..tmp_dir import TmpDir
from ..utils import soupify, stream_to_path
from .authenticators import IliasAuthenticator


class ContentTypeException(Exception):
    """Thrown when the content type of the ilias element can not be handled."""

    def __init__(self, message: str):
        """Create a new exception."""
        super().__init__(message)


class IliasDownloader():
    """A downloader for ILIAS."""

    def __init__(self, tmp_dir: TmpDir, organizer: Organizer, authenticator: IliasAuthenticator):
        """Create a new IliasDownloader."""
        self._authenticator = authenticator
        self._session = requests.Session()
        self._tmp_dir = tmp_dir
        self._organizer = organizer

    def download(self, url: str, target_path: Path, params: Dict[str, Any]) -> None:
        """Download a file from ILIAS.

        Retries authentication until eternity, if it could not fetch the file.
        """
        tmp_file = self._tmp_dir.new_file()

        while not self._try_download(url, tmp_file, params):
            self._authenticator.authenticate(self._session)

        self._organizer.accept_file(tmp_file, target_path)

    def _try_download(self, url: str, target_path: Path, params: Dict[str, Any]) -> bool:
        with self._session.get(url, params=params, stream=True) as r:
            content_type = r.headers["content-type"]

            if content_type.startswith("text/html"):
                # Dangit, we're probably not logged in.
                soup = soupify(r)

                if self._is_logged_in(soup):
                    raise ContentTypeException(
                        "Attempting to download a web page, not a file"
                    )

                return False
            else:
                # Yay, we got the file :)
                stream_to_path(r, target_path)
                return True

    def _is_logged_in(self, soup: Any) -> bool:
        userlog = soup.find("li", {"id": "userlog"})
        return userlog is not None