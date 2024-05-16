import asyncio
import signal
import jinja2
import socketio
from aiohttp import web
import aiohttp_jinja2

import libeagle

import logging
import sys
import argparse


class Eagle200Collector(object):

    def __init__(self, args):

        self.eagle_ip = args.eagle_ip
        self.eagle_cloud_id = args.eagle_cloud_id
        self.eagle_install_code = args.eagle_install_code

        self.address = args.address
        self.port = args.port
        self.interval = args.interval

        self.log = logging.getLogger("shairport-display")

        format = logging.Formatter(
            "%(asctime)s - [%(levelname)s] - %(message)s", "%Y-%m-%d %H:%M:%S"
        )

        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(format)
        handler.setLevel(logging.INFO)

        self.log.addHandler(handler)
        self.log.setLevel(logging.INFO)

        self.sio = socketio.AsyncServer(async_mode="aiohttp")
        self.sio.on("connect", self.connect, namespace="/test")
        self.sio.on("disconnect", self.disconnect, namespace="/test")

        self.clients = []

    async def run(self):
        self.log.info("Starting application")

        app = web.Application()

        app.add_routes(
            [web.get("/", self.index_handler), web.static("/static", "static")]
        )

        aiohttp_jinja2.setup(app, loader=jinja2.FileSystemLoader("templates"))

        self.sio.attach(app)

        runner = web.AppRunner(app)
        await runner.setup()

        site = web.TCPSite(runner, self.address, self.port)
        await site.start()

        try:
            async with libeagle.Connection(
                self.eagle_ip, self.eagle_cloud_id, self.eagle_install_code
            ) as conn:

                while True:
                    devices = await conn.device_list()

                    if len(devices) > 0:
                        break

                device = next(x for x in devices if x["Name"] == "Power Meter")

                while True:

                    if len(self.clients) > 0:

                        query = await conn.device_query(
                            device["HardwareAddress"],
                            "Main",
                            "zigbee:InstantaneousDemand",
                        )

                        if len(query) > 0 and len(query["Components"]) > 0:

                            component = next(
                                x for x in query["Components"] if x["Name"] == "Main"
                            )

                            demand = component["Variables"][
                                "zigbee:InstantaneousDemand"
                            ]

                            power = float(demand) * 1000.0
                            self.log.info(f"{power} W")

                            await self.sio.emit(
                                "newpower", {"power": power}, namespace="/test"
                            )

                    await asyncio.sleep(self.interval)

        except asyncio.exceptions.CancelledError:
            pass
        finally:
            for client in self.clients:
                self.log.info(f"disconnecting client: {client}")
                await self.sio.disconnect(client)

            self.log.info("Stopping application")

            await self.sio.shutdown()
            await runner.cleanup()

        self.log.info("Application stopped")

    async def connect(self, sid, environ):
        self.log.info("Client connected")

        self.clients.append(sid)

        self.log.info(f"Total clients: {len(self.clients)}")

        await self.sio.emit(
            "clients", {"clients": len(self.clients)}, namespace="/test"
        )

    async def disconnect(self, sid):
        self.log.info("Client disconnected")

        self.clients.remove(sid)

        self.log.info(f"Total clients: {len(self.clients)}")

        await self.sio.emit(
            "clients", {"clients": len(self.clients)}, namespace="/test"
        )

    @aiohttp_jinja2.template("index.html")
    async def index_handler(self, request):
        return {}


def main():
    parser = argparse.ArgumentParser(
        description="Simple web interface to display power measurements from an eagle-200",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    required = parser.add_argument_group("required arguments")
    optional = parser.add_argument_group("optional arguments")

    required.add_argument(
        "--eagle-ip",
        dest="eagle_ip",
        default=argparse.SUPPRESS,
        required=True,
        help="IP address of the eagle-200 device",
    )
    required.add_argument(
        "--eagle-cloud-id",
        dest="eagle_cloud_id",
        default=argparse.SUPPRESS,
        required=True,
        help="eagle-200 cloud id",
    )
    required.add_argument(
        "--eagle-install-code",
        dest="eagle_install_code",
        default=argparse.SUPPRESS,
        required=True,
        help="eagle-200 install code",
    )

    optional.add_argument(
        "--address",
        dest="address",
        type=str,
        default="localhost",
        help="web server listening address",
    )

    optional.add_argument(
        "--port", dest="port", type=int, default=8080, help="web server listening port"
    )

    optional.add_argument(
        "--interval", dest="interval", type=int, default=1, help="Interval in seconds"
    )

    args = parser.parse_args()

    app = Eagle200Collector(args)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    task = asyncio.ensure_future(app.run())

    for signum in (signal.SIGINT, signal.SIGQUIT, signal.SIGTERM):
        loop.add_signal_handler(signum, lambda: task.cancel())

    loop.run_until_complete(task)


if __name__ == "__main__":
    sys.exit(main())
