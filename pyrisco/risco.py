"""Implementation of a Risco Cloud connection."""

import aiohttp
import asyncio
import json
from abc import ABC, abstractmethod

LOGIN_URL = "https://www.riscocloud.com/webapi/api/auth/login"
SITE_URL = "https://www.riscocloud.com/webapi/api/wuws/site/GetAll"
PIN_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/Login"
STATE_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/GetState"
CONTROL_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/PartArm"
CONTROL_URL_PANEL_MODE = "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/Arm"
PANEL_ARM = 1
PANEL_DISARM = 0
PANEL_PARTIAL_ARM = 2

PARTITION_ARM = 3
PARTITION_DISARM = 1
PARTITION_PARTIAL_ARM = 2

EVENTS_URL = (
    "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/GetEventLog"
)
BYPASS_URL = "https://www.riscocloud.com/webapi/api/wuws/site/%s/ControlPanel/SetZoneBypassStatus"

GROUP_ID_TO_NAME = ["A", "B", "C", "D"]

NUM_RETRIES = 3

EVENT_IDS_TO_TYPES = {
    3: "triggered",
    9: "zone bypassed",
    10: "zone unbypassed",
    13: "armed",
    16: "disarmed",
    28: "power lost",
    29: "power restored",
    34: "media lost",
    35: "media restore",
    36: "service needed",
    118: "group arm",
    119: "group arm",
    120: "group arm",
    121: "group arm",
}


class Partition(ABC):
    """A representation of a Risco partition."""

    def __init__(self, raw):
        """Read partition from response."""
        self._raw = raw

    @property
    @abstractmethod
    def id(self):
        pass

    @property
    @abstractmethod
    def disarmed(self):
        pass

    @property
    @abstractmethod
    def partially_armed(self):
        pass

    @property
    @abstractmethod
    def armed(self):
        pass

    @property
    @abstractmethod
    def triggered(self):
        pass

    @property
    @abstractmethod
    def exit_timeout(self):
        pass

    @property
    def arming(self):
        """Is the partition arming."""
        return self.exit_timeout > 0

    @property
    @abstractmethod
    def groups(self):
        pass

    @property
    def panel_mode(self):
        return self._raw.get("partitions") is None

class SinglePartition(Partition):
    def __init__(self, raw):
        self._panel_mode = True
        super().__init__(raw)

    def id(self):
        return 0

    def disarmed(self):
        """Is the partition disarmed."""
        return self._raw["systemStatus"] == 0

    def partially_armed(self):
        """Is the partition partially-armed."""
        return self._raw["systemStatus"] == 4

    def armed(self):
        """Is the partition armed."""
        return self._raw["systemStatus"] == 1

    def triggered(self):
        """Is the partition triggered."""
        return self._raw["bellOn"]
    
    def exit_timeout(self):
        """Time remaining till armed."""
        return self._raw["exitDelayTimeout"]

    def groups(self):
        """Group arming status."""
        return {}

class MultiplePartition(Partition):
    def __init__(self, raw):
        self._panel_mode = False
        super().__init__(raw)

    def id(self):
        """Partition ID number."""
        return self._raw["id"]

    def disarmed(self):
        """Is the partition disarmed."""
        return self._raw["armedState"] == 1

    def partially_armed(self):
        """Is the partition partially-armed."""
        return self._raw["armedState"] == 2

    def armed(self):
        """Is the partition armed."""
        return self._raw["armedState"] == 3

    def triggered(self):
        """Is the partition triggered."""
        return self._raw["alarmState"] == 1

    def exit_timeout(self):
        """Time remaining till armed."""
        return self._raw["exitDelayTO"]

    def groups(self):
        """Group arming status."""
        if self._raw.get("groups") is None:
            return {}
        return {GROUP_ID_TO_NAME[g["id"]]: g["state"] == 3 for g in self._raw["groups"]}

class Zone:
    """A representation of a Risco zone."""

    def __init__(self, raw):
        """Read zone from response."""
        self._raw = raw

    @property
    def id(self):
        """Zone ID number."""
        return self._raw["zoneID"]

    @property
    def name(self):
        """Zone name."""
        return self._raw["zoneName"]

    @property
    def type(self):
        """Zone type."""
        return self._raw["zoneType"]

    @property
    def triggered(self):
        """Is the zone triggered."""
        return self._raw["status"] == 1

    @property
    def bypassed(self):
        """Is the zone triggered."""
        return self._raw["status"] == 2


class Alarm:
    """A representation of a Risco alarm system."""

    def __init__(self, raw):
        """Read alarm from response."""
        self._raw = raw
        self._partitions = None
        self._zones = None

    @property
    def partitions(self):
        """Alarm partitions."""
        if self._partitions is None:
            if self._raw["partitions"] is not None:
                print('Partitions exists')
                self._partitions = {p["id"]: MultiplePartition(p) for p in self._raw["partitions"]}
            else:
                print('Partitions not exists create a single partition')
                self._partitions = {0: SinglePartition(self._raw)}
        return self._partitions

    @property
    def zones(self):
        """Alarm zones."""
        if self._zones is None:
            self._zones = {z["zoneID"]: Zone(z) for z in self._raw["zones"]}
        return self._zones

class Event:
    """A representation of a Risco event."""

    def __init__(self, raw):
        """Read event from response."""
        self._raw = raw

    @property
    def raw(self):
        return self._raw

    @property
    def type_id(self):
        return self.raw["eventId"]

    @property
    def type_name(self):
        return EVENT_IDS_TO_TYPES.get(self.type_id, "unknown"),

    @property
    def partition_id(self):
        partition_id = self.raw["partAssociationCSV"]
        if partition_id is None:
            return None
        return int(partition_id)

    @property
    def time(self):
        """Time the event was fired."""
        return self.raw["logTime"]

    @property
    def text(self):
        """Event text."""
        return self.raw["eventText"]

    @property
    def name(self):
        """Event name."""
        return self.raw["eventName"]

    @property
    def category_id(self):
        """Event group number."""
        return self.raw["group"]

    @property
    def category_name(self):
        """Event group number."""
        return self.raw["groupName"]

    @property
    def zone_id(self):
        if self.raw["sourceType"] == 1:
            return self.raw["sourceID"] - 1
        return None

    @property
    def user_id(self):
        if self.raw["sourceType"] == 2:
            return self.raw["sourceID"]
        return None

    @property
    def group(self):
        if self.type_id in range(118, 122):
            return GROUP_ID_TO_NAME[self.type_id - 118]
        return None

    @property
    def priority(self):
        return self.raw["priority"]

    @property
    def source_id(self):
        return self._source_id

class RiscoAPI:
    """A connection to a Risco alarm system."""

    def __init__(self, username, password, pin, language="en"):
        """Initialize the object."""
        self._username = username
        self._password = password
        self._pin = pin
        self._language = language
        self._access_token = None
        self._session_id = None
        self._site_id = None
        self._site_name = None
        self._site_uuid = None
        self._session = None
        self._created_session = False
        self._control_url = None
        self._state_body_template = None
        self._group_body_template = None
        self._state_arm = None
        self._state_disarm = None
        self._state_partial_arm = None

    async def _authenticated_post(self, url, body):
        headers = {
            "Content-Type": "application/json",
            "authorization": "Bearer " + self._access_token,
        }
        async with self._session.post(url, headers=headers, json=body) as resp:
            json = await resp.json()

        if json["status"] == 401:
            raise UnauthorizedError(json["errorText"])

        if "result" in json and json["result"] != 0:
            raise OperationError(str(json))

        return json["response"]

    async def _site_post(self, url, body):
        site_url = url % self._site_id
        for i in range(NUM_RETRIES):
            try:
                site_body = {
                    **body,
                    "fromControlPanel": True,
                    "sessionToken": self._session_id,
                }
                return await self._authenticated_post(site_url, site_body)
            except UnauthorizedError:
                if i + 1 == NUM_RETRIES:
                    raise
                await self.close()
                await self.login()

    async def _login_user_pass(self):
        headers = {"Content-Type": "application/json"}
        body = {"userName": self._username, "password": self._password}
        try:
            async with self._session.post(
                LOGIN_URL, headers=headers, json=body
            ) as resp:
                json = await resp.json()
                if json["status"] == 401:
                    raise UnauthorizedError("Invalid username or password")
                self._access_token = json["response"].get("accessToken")
        except aiohttp.client_exceptions.ClientConnectorError as e:
            raise CannotConnectError from e

        if not self._access_token:
            raise UnauthorizedError("Invalid username or password")

    async def _login_site(self):
        resp = await self._authenticated_post(SITE_URL, {})
        self._site_id = resp[0]["id"]
        self._site_name = resp[0]["name"]
        self._site_uuid = resp[0]["siteUUID"]

    async def _login_session(self):
        body = {"languageId": self._language, "pinCode": self._pin}
        url = PIN_URL % self._site_id
        resp = await self._authenticated_post(url, body)
        self._session_id = resp["sessionId"]

    async def _init_session(self, session):
        await self.close()
        if self._session is None:
            if session is None:
                self._session = aiohttp.ClientSession()
                self._created_session = True
            else:
                self._session = session

    async def _init_system_partion_type(self):
        alarm = await self.get_state()
        #if (alarm.partitions is None)
         #   raise OperationError(str(alarm))
        first_partition = list(alarm.partitions.values())[0]
        print(first_partition)
        if (first_partition.panel_mode):
            self._control_url = CONTROL_URL_PANEL_MODE
            self._state_body_template =  "{{\"newSystemStatus\": {1} }}"
            self._group_body_template  =  "{{\"newSystemStatus\": {1} }}"
            self._state_arm = PANEL_ARM
            self._state_disarm = PANEL_DISARM
            self._state_partial_arm = PANEL_PARTIAL_ARM

        else:
            self._control_url = CONTROL_URL
            self._state_body_template = '{"partitions": [{"id": {0}, "armedState": {1}}],}'
            self._group_body_template  =  '{"partitions": [{"id": {0}, "groups": [{"id": {1}, "state": {2}}]}],}'
            self._state_arm = PARTITION_ARM
            self._state_disarm = PARTITION_DISARM
            self._state_partial_arm = PARTITION_PARTIAL_ARM

    async def close(self):
        """Close the connection."""
        self._session_id = None
        if self._created_session == True and self._session is not None:
            await self._session.close()
            self._session = None
            self._created_session = False

    async def login(self, session=None):
        """Login to Risco Cloud."""
        if self._session_id:
            return

        await self._init_session(session)
        await self._login_user_pass()
        await self._login_site()
        await self._login_session()
        await self._init_system_partion_type()

    async def get_state(self):
        """Get partitions and zones."""
        resp = await self._site_post(STATE_URL, {})
        return Alarm(resp["state"]["status"])

    async def disarm(self, partition):
        """Disarm the alarm."""
        body = json.loads(self._state_body_template.format(partition, self._state_disarm))
        return Alarm(await self._site_post(self._control_url, body))

    async def arm(self, partition):
        """Arm the alarm."""
        body = json.loads(self._state_body_template.format(partition, self._state_arm))
        return Alarm(await self._site_post(self._control_url, body))

    async def partial_arm(self, partition):
        """Partially-arm the alarm."""
        body = json.loads(self._state_body_template.format(partition,self._state_partial_arm))
        return Alarm(await self._site_post(self._control_url, body))

    async def group_arm(self, partition, group):
        """Arm a specific group."""
        if isinstance(group, str):
            group = GROUP_ID_TO_NAME.index(group)

        body = json.loads(self._group_body_template.format( partition, group, self._state_arm))
        return Alarm(await self._site_post(self._control_url, body))

    async def get_events(self, newer_than, count=10):
        """Get event log."""
        body = {
            "count": count,
            "newerThan": newer_than,
            "offset": 0,
        }
        response = await self._site_post(EVENTS_URL, body)
        return [Event(e) for e in response["controlPanelEventsList"]]

    async def bypass_zone(self, zone, bypass):
        """Bypass or unbypass a zone."""
        status = 2 if bypass else 3
        body = {"zones": [{"trouble": 0, "ZoneID": zone, "Status": status}]}
        return Alarm(await self._site_post(BYPASS_URL, body))

    @property
    def site_id(self):
        """Site ID of the Alarm instance."""
        return self._site_id

    @property
    def site_name(self):
        """Site name of the Alarm instance."""
        return self._site_name

    @property
    def site_uuid(self):
        """Site UUID of the Alarm instance."""
        return self._site_uuid

class UnauthorizedError(Exception):
    """Exception to indicate an error in authorization."""

class CannotConnectError(Exception):
    """Exception to indicate an error in authorization."""

class OperationError(Exception):
    """Exception to indicate an error in operation."""
