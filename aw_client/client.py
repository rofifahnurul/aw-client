import functools
import json
import logging
import os
import socket
import getpass
import threading
from collections import namedtuple
from datetime import datetime
from time import sleep
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Union,
)
import time
import requests
import persistqueue
import requests as req
from aw_core.dirs import get_data_dir
from aw_core.models import Event

from .config import load_config
from .singleinstance import SingleInstance

# FIXME: This line is probably badly placed
logging.getLogger("requests").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def _log_request_exception(e: req.RequestException):
    r = e.response
    logger.warning(str(e))
    try:
        d = r.json()
        logger.warning(f"Error message received: {d}")
    except json.JSONDecodeError:
        pass


def _dt_is_tzaware(dt: datetime) -> bool:
    return dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) is not None


def always_raise_for_request_errors(f: Callable[..., req.Response]):
    @functools.wraps(f)
    def g(*args, **kwargs):
        r = f(*args, **kwargs)
        try:
            r.raise_for_status()
        except req.RequestException as e:
            _log_request_exception(e)
            raise e
        return r

    return g

def check_modification_time(file_path):
    try:
        six_days_in_seconds = 6 * 24 * 60 * 60
        #six_days_in_seconds = 60
        modified_time = os.path.getmtime(file_path)
        current_time = time.time()
        time_difference = current_time - modified_time

        return time_difference >= six_days_in_seconds
        
    except OSError:
        print(f"Error: Unable to get the modification time of the file '{file_path}'")
        return None
    
def send_request_token(hostname,server,file_path):
     # If the file is missing, create a new file and send the registration request
    data = {
        "hostname": hostname,
        # Add other registration fields as needed
    }
       
    try:
        headers = {"Content-Type": "application/json"}
        response = requests.post(server, json=data, headers=headers)
        response.raise_for_status()  # Raise an exception for HTTP errors
        response_data = response.json()  # Parse the JSON response data
        
            # You can access specific fields from the response_data dictionary if needed
            
        hostname = response_data.get("hostname")
        token= response_data.get("token")

        with open(file_path, "w") as file:
            file.write(token)
        return token
            
    except requests.exceptions.RequestException as e:
            print("Failed to register:", e)

def check_file(hostname,server):
    file_path = "required_file.txt" 
   
    if os.path.exists(file_path):

        modification_time = check_modification_time(file_path)
        print("MODIFICATION TIME", modification_time)
        if modification_time == True:
            send_request_token(hostname,server,file_path)
            print("MODIF TRUE RUN")
        else:
            # If the file exists, open it and read its contents
            with open(file_path, "r") as file:
                token = file.read()
                return token

    else:
        send_request_token(hostname,server,file_path)
       

def run_check_file(func):
    @functools.wraps(func)
    def wrapper(self, *args, headers=None, **kwargs):
        default_headers = {"Content-type": "application/json", "charset": "utf-8"}
        token = check_file(self.client_hostname, "http://127.0.0.1:5600/register")
        if token:
            default_headers["Authorization"] = token
        # Merge the default headers with the headers provided as an argument
        #print("\n Token:",token)
        final_headers = default_headers.copy()
        if headers is not None:
            final_headers.update(headers)

        # Call the function with the updated headers, without changing the argument name
        return func(self, *args, headers=final_headers, **kwargs)
    return wrapper


class ActivityWatchClient:
    def __init__(
        self,
        client_name: str = "unknown",
        testing=False,
        host=None,
        port=None,
        protocol="http",
    ) -> None:
        """
        A handy wrapper around the aw-server REST API. The recommended way of interacting with the server.

        Can be used with a `with`-statement as an alternative to manually calling connect and disconnect in a try-finally clause.

        :Example:

        .. literalinclude:: examples/client.py
            :lines: 7-
        """
        
        self.testing = testing

        self.client_name = client_name
        self.client_username = getpass.getuser()
        self.client_hostname = socket.gethostname()

        _config = load_config()
        server_config = _config["server" if not testing else "server-testing"]
        client_config = _config["client" if not testing else "client-testing"]

        server_host = host or server_config["hostname"]
        server_port = port or server_config["port"]
        self.server_address = "{protocol}://{host}:{port}".format(
            protocol=protocol, host=server_host, port=server_port
        )
        check_file(self.client_hostname, "http://127.0.0.1:5600/register")
        self.instance = SingleInstance(
            f"{self.client_name}-at-{server_host}-on-{server_port}"
        )

        self.commit_interval = client_config["commit_interval"]

        self.request_queue = RequestQueue(self)
        # Dict of each last heartbeat in each bucket
        self.last_heartbeat = {}  # type: Dict[str, Event]

    #
    #   Get/Post base requests
    #

    def _url(self, endpoint: str):
        return f"{self.server_address}/api/0/{endpoint}"

    @run_check_file
    @always_raise_for_request_errors
    def _get(self, endpoint: str, params: Optional[dict] = None, headers=None) -> req.Response:
        #print("HEADERS GET",headers)
        return req.get(self._url(endpoint), params=params, headers=headers)

    @run_check_file
    @always_raise_for_request_errors
    def _post(
        self,
        endpoint: str,
        data: Union[List[Any], Dict[str, Any]],
        params: Optional[dict] = None,
        headers=str,
    ) -> req.Response:
        #headers = {"Content-type": "application/json", "charset": "utf-8", "Authorization":token}
        # print("POST Request:")
        # print("Endpoint:", self._url(endpoint))
        print("Data:", data)
        print("Headers:", headers)
        # print("Params:", params)
        # print("HEADERS POST",headers)
        response = req.post(
            self._url(endpoint),
            data=bytes(json.dumps(data), "utf8"),
            headers=headers,
            params=params,
        )
        # print("Response:")
        # print("Status Code:", response.status_code)
        # print("Response Headers:", response.headers)
        # print("Response Content:", response.text)
        return response

    @run_check_file
    @always_raise_for_request_errors
    def _delete(self, endpoint: str, data: Any = dict(), headers=None) -> req.Response:
        if headers is None:
            headers = {}
        #print("HEADERS DELETE",headers)
        return req.delete(self._url(endpoint), data=json.dumps(data), headers=headers)

    def get_info(self):
        """Returns a dict currently containing the keys 'hostname' and 'testing'."""
        endpoint = "info"
        return self._get(endpoint).json()

    #
    #   Event get/post requests
    #

    def get_event(
        self,
        bucket_id: str,
        event_id: int,
    ) -> Optional[Event]:
        endpoint = f"buckets/{bucket_id}/events/{event_id}"
        try:
            event = self._get(endpoint).json()
            return Event(**event)
        except req.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                return None
            else:
                raise

    def get_events(
        self,
        bucket_id: str,
        limit: int = -1,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> List[Event]:
        endpoint = f"buckets/{bucket_id}/events"

        params = dict()  # type: Dict[str, str]
        if limit is not None:
            params["limit"] = str(limit)
        if start is not None:
            params["start"] = start.isoformat()
        if end is not None:
            params["end"] = end.isoformat()

        events = self._get(endpoint, params=params).json()
        return [Event(**event) for event in events]

    def insert_event(self, bucket_id: str, event: Event) -> None:
        endpoint = f"buckets/{bucket_id}/events"
        data = [event.to_json_dict()]
        self._post(endpoint, data)

    def insert_events(self, bucket_id: str, events: List[Event]) -> None:
        endpoint = f"buckets/{bucket_id}/events"
        data = [event.to_json_dict() for event in events]
        self._post(endpoint, data)

    def delete_event(self, bucket_id: str, event_id: int) -> None:
        endpoint = f"buckets/{bucket_id}/events/{event_id}"
        self._delete(endpoint)

    def get_eventcount(
        self,
        bucket_id: str,
        limit: int = -1,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> int:
        endpoint = f"buckets/{bucket_id}/events/count"

        params = dict()  # type: Dict[str, str]
        if start is not None:
            params["start"] = start.isoformat()
        if end is not None:
            params["end"] = end.isoformat()

        response = self._get(endpoint, params=params)
        return int(response.text)

    def heartbeat(
        self,
        bucket_id: str,
        event: Event,
        pulsetime: float,
        queued: bool = False,
        commit_interval: Optional[float] = None,
    ) -> None:
        """
        Args:
            bucket_id: The bucket_id of the bucket to send the heartbeat to
            event: The actual heartbeat event
            pulsetime: The maximum amount of time in seconds since the last heartbeat to be merged with the previous heartbeat in aw-server
            queued: Use the aw-client queue feature to queue events if client loses connection with the server
            commit_interval: Override default pre-merge commit interval

        NOTE: This endpoint can use the failed requests retry queue.
              This makes the request itself non-blocking and therefore
              the function will in that case always returns None.
        """

        from aw_transform.heartbeats import heartbeat_merge

        # print("queued: ",queued)
        endpoint = f"buckets/{bucket_id}/heartbeat?pulsetime={pulsetime}"
        _commit_interval = commit_interval or self.commit_interval

        # print("bucket_id: ",bucket_id)
        # print("pulsetime: ",pulsetime)
        if queued:
            # Pre-merge heartbeats
            if bucket_id not in self.last_heartbeat:
                # print("if bucket_id not in self.last_heartbeat")
                self.last_heartbeat[bucket_id] = event
                return None

            last_heartbeat = self.last_heartbeat[bucket_id]

            merge = heartbeat_merge(last_heartbeat, event, pulsetime)

            if merge:
                # If last_heartbeat becomes longer than commit_interval
                # then commit, else cache merged.
                diff = (last_heartbeat.duration).total_seconds()
                # print("Time Difference:", diff)

                if diff >= _commit_interval:
                    data = merge.to_json_dict()
                    #print("Merged Heartbeat to be committed:", data)
                    self.request_queue.add_request(endpoint, data)
                    self.last_heartbeat[bucket_id] = event
                else:
                    self.last_heartbeat[bucket_id] = merge
                    # print("Merged Heartbeat cached:", merge)
            else:
                data = last_heartbeat.to_json_dict()
                # print("Last Heartbeat to be committed:", data)
                self.request_queue.add_request(endpoint, data)
                self.last_heartbeat[bucket_id] = event
        else:
            # print("Not Queued. Posting heartbeat directly.")
            self._post(endpoint, event.to_json_dict())

    #
    #   Bucket get/post requests
    #

    def get_buckets(self) -> dict:
        return self._get("buckets/").json()


    def create_bucket(self, bucket_id: str, event_type: str, queued=False):        
        if queued:
            self.request_queue.register_bucket(bucket_id, event_type)
        else:
            endpoint = f"buckets/{bucket_id}"
            data = {
                "client": self.client_name,
                "hostname": self.client_hostname,
                "name" : self.client_username,
                "type": event_type,
            }
            
            self._post(endpoint, data)
            

    def delete_bucket(self, bucket_id: str, force: bool = False):
        self._delete(f"buckets/{bucket_id}" + ("?force=1" if force else ""))

    # @deprecated
    def setup_bucket(self, bucket_id: str, event_type: str):
        self.create_bucket(bucket_id, event_type, queued=True)

    # Import & export

    def export_all(self) -> dict:
        return self._get("export").json()

    def export_bucket(self, bucket_id) -> dict:
        return self._get(f"buckets/{bucket_id}/export").json()

    def import_bucket(self, bucket: dict) -> None:
        endpoint = "import"
        self._post(endpoint, {"buckets": {bucket["id"]: bucket}})

    #
    #   Query (server-side transformation)
    #

    def query(
        self,
        query: str,
        timeperiods: List[Tuple[datetime, datetime]],
        name: Optional[str] = None,
        cache: bool = False,
    ) -> List[Any]:
        endpoint = "query/"
        params = {}  # type: Dict[str, Any]
        if cache:
            if not name:
                raise Exception(
                    "You are not allowed to do caching without a query name"
                )
            params["name"] = name
            params["cache"] = int(cache)

        # Check that datetimes have timezone information
        for start, stop in timeperiods:
            try:
                assert _dt_is_tzaware(start)
                assert _dt_is_tzaware(stop)
            except AssertionError:
                raise ValueError("start/stop needs to have a timezone set")

        data = {
            "timeperiods": [
                "/".join([start.isoformat(), end.isoformat()])
                for start, end in timeperiods
            ],
            "query": query.split("\n"),
        }
        response = self._post(endpoint, data, params=params)
        return response.json()

    #
    #   Connect and disconnect
    #

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()

    def connect(self):
        if not self.request_queue.is_alive():
            self.request_queue.start()

    def disconnect(self):
        self.request_queue.stop()
        self.request_queue.join()

        # Throw away old thread object, create new one since same thread cannot be started twice
        self.request_queue = RequestQueue(self)


QueuedRequest = namedtuple("QueuedRequest", ["endpoint", "data"])
Bucket = namedtuple("Bucket", ["id", "type"])


class RequestQueue(threading.Thread):
    """Used to asynchronously send heartbeats.

    Handles:
        - Cases where the server is temporarily unavailable
        - Saves all queued requests to file in case of a server crash
    """

    VERSION = 1  # update this whenever the queue-file format changes

    def __init__(self, client: ActivityWatchClient) -> None:
        threading.Thread.__init__(self, daemon=True)

        self.client = client

        self.connected = False
        self._stop_event = threading.Event()

        # Buckets that will have events queued to them, will be created if they don't exist
        self._registered_buckets = []  # type: List[Bucket]

        self._attempt_reconnect_interval = 10

        # Setup failed queues file
        data_dir = get_data_dir("aw-client")
        queued_dir = os.path.join(data_dir, "queued")
        if not os.path.exists(queued_dir):
            os.makedirs(queued_dir)

        persistqueue_path = os.path.join(
            queued_dir,
            "{}{}.v{}.persistqueue".format(
                self.client.client_name,
                "-testing" if client.testing else "",
                self.VERSION,
            ),
        )

        logger.debug(f"queue path '{persistqueue_path}'")

        self._persistqueue = persistqueue.FIFOSQLiteQueue(
            persistqueue_path, multithreading=True, auto_commit=False
        )
        self._current = None  # type: Optional[QueuedRequest]

    def _get_next(self) -> Optional[QueuedRequest]:
        # self._current will always hold the next not-yet-sent event,
        # until self._task_done() is called.
        if not self._current:
            try:
                self._current = self._persistqueue.get(block=False)
            except persistqueue.exceptions.Empty:
                return None
        return self._current

    def _task_done(self) -> None:
        self._current = None
        self._persistqueue.task_done()

    def _create_buckets(self) -> None:
        for bucket in self._registered_buckets:
            self.client.create_bucket(bucket.id, bucket.type)

    def _try_connect(self) -> bool:
        try:  # Try to connect
            self._create_buckets()
            self.connected = True
            logger.info(
                "Connection to aw-server established by {}".format(
                    self.client.client_name
                )
            )
        except req.RequestException:
            self.connected = False

        return self.connected

    def wait(self, seconds) -> bool:
        return self._stop_event.wait(seconds)

    def should_stop(self) -> bool:
        return self._stop_event.is_set()

    def _dispatch_request(self) -> None:
        request = self._get_next()
        if not request:
            self.wait(0.2)  # seconds to wait before re-polling the empty queue
            return

        try:
            self.client._post(request.endpoint, request.data)
        except req.exceptions.ConnectTimeout:
            # Triggered by:
            #   - server not running (connection refused)
            #   - server not responding (timeout)
            # Safe to retry according to requests docs:
            #   https://requests.readthedocs.io/en/latest/api/#requests.ConnectTimeout

            self.connected = False
            logger.warning(
                "Connection refused or timeout, will queue requests until connection is available."
            )
            # wait a bit before retrying, so we don't spam the server (or logs), see:
            #  - https://github.com/ActivityWatch/activitywatch/issues/815
            #  - https://github.com/ActivityWatch/activitywatch/issues/756#issuecomment-1266662861
            sleep(0.5)
            return
        except req.RequestException as e:
            if e.response and e.response.status_code == 400:
                # HTTP 400 - Bad request
                # Example case: https://github.com/ActivityWatch/activitywatch/issues/815
                # We don't want to retry, because a bad payload is likely to fail forever.
                logger.error(f"Bad request, not retrying: {request.data}")
            elif e.response and e.response.status_code == 500:
                # HTTP 500 - Internal server error
                # It is possible that the server is in a bad state (and will recover on restart),
                # in which case we want to retry. I hope this can never caused by a bad payload.
                logger.error(f"Internal server error, retrying: {request.data}")
                sleep(0.5)
                return
            else:
                logger.exception(f"Unknown error, not retrying: {request.data}")
        except Exception:
            logger.exception(f"Unknown error, not retrying: {request.data}")

        # Mark the request as done
        self._task_done()

    def run(self) -> None:
        self._stop_event.clear()
        while not self.should_stop():
            # Connect
            while not self._try_connect():
                logger.warning(
                    "Not connected to server, {} requests in queue".format(
                        self._persistqueue.qsize()
                    )
                )
                if self.wait(self._attempt_reconnect_interval):
                    break

            # Dispatch requests until connection is lost or thread should stop
            while self.connected and not self.should_stop():
                self._dispatch_request()

    # def _dispatch_request(self) -> None:
    #     request = self._get_next()
    #     if not request:
    #         self.wait(0.2)  # seconds to wait before re-polling the empty queue
    #         return

    #     try:
    #         self.client._post(request.endpoint, request.data)
    #         # print("Request dispatched successfully:", request.endpoint, request.data)
    #     except req.exceptions.ConnectTimeout:
    #         # Rest of the code
    #         return "Connection TimeOut"
    #     except req.RequestException as e:
    #         # Rest of the code
    #         return "Request Exception"
    #     except Exception:
    #         # Rest of the code
    #         return "Exception"

    def stop(self) -> None:
        self._stop_event.set()

    def add_request(self, endpoint: str, data: dict) -> None:
        """
        Add a request to the queue.
        NOTE: Only supports heartbeats
        """
        assert "/heartbeat" in endpoint
        assert isinstance(data, dict)
        self._persistqueue.put(QueuedRequest(endpoint, data))

    def register_bucket(self, bucket_id: str, event_type: str) -> None:
        self._registered_buckets.append(Bucket(bucket_id, event_type))
