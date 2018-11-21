import aiohttp
import asyncio
import bs4
import getpass
import logging
import time
import urllib.parse

from .ReadWriteLock import ReadWriteLock

__all__ = [
	"OutOfTriesException",
	"ShibbolethAuthenticator",
]
logger = logging.getLogger(__name__)

class OutOfTriesException(Exception):
	pass

class ShibbolethAuthenticator:

	RETRY_ATTEMPTS = 5
	RETRY_DELAY = 1 # seconds

	def __init__(self, cookie_path=None):
		self._cookie_path = cookie_path

		# Authentication and file/page download should not happen at the same time.
		# Authenticating counts as writing, file/page downloads as reading.
		self._lock = ReadWriteLock()

		# Only one self._authenticate() should be started, even if multiple self.get_page()s
		# notice they're logged in.
		# If self._event is not None, authenticating is currently in progress.
		self._event = None

		jar = aiohttp.CookieJar()
		if self._cookie_path is not None:
			try:
				jar.load(self._cookie_path)
			except FileNotFoundError:
				pass
		self._session = aiohttp.ClientSession(cookie_jar=jar)

	async def close(self):
		await self._session.close()

	async def _post(self, url, params=None, data=None):
		for t in range(self.RETRY_ATTEMPTS):
			try:
				async with self._session.post(url, params=params, data=data) as resp:
					text = await resp.text()
					return resp.url, text
			except aiohttp.client_exceptions.ServerDisconnectedError:
				logger.debug(f"Try {t+1} out of {self.RETRY_ATTEMPTS} failed, retrying in {self.RETRY_DELAY} s")
				await asyncio.sleep(self.RETRY_DELAY)

		logger.error("Could not retrieve url")
		raise OutOfTriesException(f"Try {self.RETRY_ATTEMPTS} out of {self.RETRY_ATTEMPTS} failed.")

	async def _get(self, url, params=None):
		for t in range(self.RETRY_ATTEMPTS):
			try:
				async with self._session.get(url, params=params) as resp:
					text = await resp.text()
					return resp.url, text
			except aiohttp.client_exceptions.ServerDisconnectedError:
				logger.debug(f"Try {t+1} out of {self.RETRY_ATTEMPTS} failed, retrying in {self.RETRY_DELAY} s")
				await asyncio.sleep(self.RETRY_DELAY)

		logger.error("Could not retrieve url")
		raise OutOfTriesException(f"Try {self.RETRY_ATTEMPTS} out of {self.RETRY_ATTEMPTS} failed.")

	def _login_successful(self, soup):
		saml_response = soup.find("input", {"name": "SAMLResponse"})
		relay_state = soup.find("input", {"name": "RelayState"})
		return saml_response is not None and relay_state is not None

	def _save_cookies(self):
		logger.info(f"Saving cookies to {self._cookie_path!r}")
		if self._cookie_path is not None:
			self._session.cookie_jar.save(self._cookie_path)

	# WARNING: Only use self._ensure_authenticated() to authenticate,
	# don't call self._authenticate() itself.
	async def _authenticate(self):
		async with self._lock.write():
			# Equivalent: Click on "Mit KIT-Account anmelden" button in
			# https://ilias.studium.kit.edu/login.php
			url = "https://ilias.studium.kit.edu/Shibboleth.sso/Login"
			data = {
				"sendLogin": "1",
				"idp_selection": "https://idp.scc.kit.edu/idp/shibboleth",
				"target": "/shib_login.php",
				"home_organization_selection": "Mit KIT-Account anmelden",
			}
			logger.debug("Begin authentication process with ILIAS")
			url, text = await self._post(url, data=data)
			soup = bs4.BeautifulSoup(text, "html.parser")

			# Attempt to login using credentials, if necessary
			while not self._login_successful(soup):
				form = soup.find("form", {"class": "form2", "method": "post"})
				action = form["action"]

				print("Please enter Shibboleth credentials.")
				username = getpass.getpass(prompt="Username: ")
				password = getpass.getpass(prompt="Password: ")

				# Equivalent: Enter credentials in
				# https://idp.scc.kit.edu/idp/profile/SAML2/Redirect/SSO
				url = "https://idp.scc.kit.edu" + action
				data = {
					"_eventId_proceed": "",
					"j_username": username,
					"j_password": password,
				}
				logger.debug("Attempt to log in to Shibboleth using credentials")
				url, text = await self._post(url, data=data)
				soup = bs4.BeautifulSoup(text, "html.parser")

				if not self._login_successful(soup):
					print("Incorrect credentials.")

			# Saving progress: Successfully authenticated with Shibboleth
			self._save_cookies()

			relay_state = soup.find("input", {"name": "RelayState"})["value"]
			saml_response = soup.find("input", {"name": "SAMLResponse"})["value"]

			# Equivalent: Being redirected via JS automatically
			# (or clicking "Continue" if you have JS disabled)
			url = "https://ilias.studium.kit.edu/Shibboleth.sso/SAML2/POST"
			data = {
				"RelayState": relay_state,
				"SAMLResponse": saml_response,
			}
			logger.debug("Redirect back to ILIAS with login information")
			url, text = await self._post(url, data=data)

			# Saving progress: Successfully authenticated with Ilias
			self._save_cookies()

	async def _ensure_authenticated(self):
		if self._event is None:
			self._event = asyncio.Event()
			logger.info("Not logged in, authentication required.")
			await self._authenticate()
			self._event.set()
			self._event = None
		else:
			await self._event.wait()

	def _is_logged_in(self, soup):
		userlog = soup.find("li", {"id": "userlog"})
		return userlog is not None

	async def get_webpage(self, ref_id):
		url = "https://ilias.studium.kit.edu/goto.php"
		params = {"target": f"fold_{ref_id}"}

		while True:
			async with self._lock.read():
				logger.debug(f"Getting {url} {params}")
				_, text = await self._get(url, params=params)
				soup = bs4.BeautifulSoup(text, "html.parser")

			if self._is_logged_in(soup):
				return soup
			else:
				await self._ensure_authenticated()

	async def download_file(self, file_id):
		async with self._lock.read():
			pass # TODO
