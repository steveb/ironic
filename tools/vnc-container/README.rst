=============
VNC Container
=============

Overview
--------

This allows a container image to be built which supports Ironic's graphical
console functionality.

For each node with an enabled graphical console, the service ironic-novncproxy
(or nova-novncproxy) will connect to a VNC server exposed by a container
running this image.

Building and using
------------------

To build the container image for local use, install ``buildah`` and run the
following as the user which runs ironic-conductor::

    buildah bud -f ./Containerfile.ubuntu -t localhost/ironic-vnc-container

The ``systemd`` container provider (or an external provider) can then be configured
to use this image in ``ironic.conf``:

.. code-block:: ini

      [vnc]
      enabled = True
      container_provider=systemd
      console_image=localhost/ironic-vnc-container

Connection count
----------------

The image installs ``/usr/local/bin/connection-count``, which prints the number
of established inbound VNC connections. This absolute path is part of the
container image contract: ``stop-firefox.sh`` and the ``systemd``,
``container`` and ``kubernetes`` console container providers execute it.

The script takes no arguments. It reads ``VNC_PORT`` from the container
environment, defaulting to ``5900``, and runs::

    ss --no-header state established "sport = :${VNC_PORT:-5900}"

Only inbound sockets (``sport``) are counted. A future console container which
makes an outbound VNC connection to a BMC must not have that connection counted
as a user.

On success the script prints a single integer to stdout and exits ``0``. It
exits non-zero if ``VNC_PORT`` is not numeric or if ``ss`` fails or is missing,
and prints the error to stderr. Consumers treat a non-zero exit as an unknown
count: ``stop-firefox.sh`` assumes no clients and shuts the browser down, and
ironic-conductor assumes no activity so the session expires as before.

A custom image must provide this executable at the same absolute path. ``ss``
from ``iproute2`` must be present.


Implementation
--------------

When the container is started the following occurs:

1. x11vnc is run, which exposes a VNC server port

When a VNC connection is established, the following occurs:

1. Xvfb is run, which starts a virtual X11 session
2. A firefox browser is started in kiosk mode
3. A firefox extension automates loading the requested console app
4. For the ``fake`` app, display drivers/fake/index.html
5. For the ``redfish-graphical`` app, detect the vendor by looking at the
   ``Oem`` value in a ``/redfish/v1`` response
6. Runs vendor specific scripts to display an HTML5 based console

Multiple VNC connections can share a single instance. ``stop-firefox.sh`` uses
``/usr/local/bin/connection-count`` to detect when the last VNC connection is
closed, and then closes the running Firefox.

Vendor specific implementations are as follows.

Dell iDRAC
~~~~~~~~~~

One-time console credentials are created with a call to
``/Managers/<manager>/Oem/Dell/DelliDRACCardService/Actions/DelliDRACCardService.GetKVMSession``
and the browser loads a console URL using those credentials.

HPE iLO
~~~~~~~

The ``/irc.html`` URL is loaded. For iLO 6 the inline login form is populated
with credentials and submitted, showing the console. For iLO 5 the main login
page is loaded, and when the login is submitted ``irc.html`` is loaded again.

Supermicro (Experimental)
~~~~~~~~~~~~~~~~~~~~~~~~~

A simulated user logs in, waits for the console preview image to load, then
clicks on it.

