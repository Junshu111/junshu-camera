"""Real retained-file + loopback HTTP contract tests; no camera or GPIO access."""
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import http.client
import socket
import shutil
import subprocess
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

from PIL import Image
from camera_r4 import photo_share, share_page

try:
    from camera_r4.photo_share import PhotoShareSession
except ModuleNotFoundError:
    PhotoShareSession = None

QA = Path(__file__).resolve().parents[2] / 'qa' / 'r4-transfer-20261025'


class PhotoShareTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(PhotoShareSession, 'PhotoShareSession feature is missing')
        QA.mkdir(parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix='share-fixture-', dir=QA))
        self.photos = self.directory / 'photos'
        self.photos.mkdir()
        self.paths = []
        for index, color in enumerate(('red', 'green', 'blue')):
            path = self.photos / ('JC_test_{}.JPG'.format(index))
            Image.new('RGB', (32, 24), color).save(path, 'JPEG')
            self.paths.append(path)
        self.sessions = []

    def tearDown(self):
        for session in getattr(self, 'sessions', []):
            session.close()
        # Fixtures intentionally retained. No unlink/rmtree/TemporaryDirectory.

    def start(self, paths=None, ttl_seconds=900):
        result = PhotoShareSession.start(self.photos, self.paths[:2] if paths is None else paths,
                                         '127.0.0.1', ttl_seconds=ttl_seconds)
        self.sessions.append(result)
        return result

    def get(self, session, route='', headers=None):
        return urlopen(Request(session.url + route, headers=headers or {}), timeout=3)

    def assert_denied(self, session, route, headers=None, expected=None):
        try:
            with self.get(session, route, headers) as response:
                result = response.read()
                self.fail('Unauthorized route returned {} bytes: {}'.format(len(result), route))
        except HTTPError as error:
            if expected is not None:
                self.assertEqual(error.code, expected)
            self.assertNotIn(self.paths[0].read_bytes(), error.read())

    def test_original_is_authorized_bytes_and_has_security_headers(self):
        session = self.start()
        with self.get(session, 'original/0') as response:
            self.assertEqual(response.read(), self.paths[0].read_bytes())
            self.assertEqual(response.headers['Content-Type'], 'image/jpeg')
            self.assertIn('attachment', response.headers['Content-Disposition'])
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            self.assertIn("default-src 'none'", response.headers['Content-Security-Policy'])
            self.assertIsNone(response.headers.get('Access-Control-Allow-Origin'))
        deadline = time.monotonic() + 1
        while session.status()['downloads_finished'] < 1 and time.monotonic() < deadline:
            time.sleep(.01)
        state = session.status()
        self.assertEqual((state['count'], state['downloads_started'], state['downloads_finished']), (2, 1, 1))

    def test_unselected_and_traversal_and_wrong_token_cannot_read_files(self):
        session = self.start()
        for route in ('original/2', 'original/-1', 'original/01', 'original/0/extra',
                      '../JC_test_2.JPG', 'original/%2e%2e%2fJC_test_2.JPG',
                      'original/%30', '?anything=1'):
            with self.subTest(route=route):
                self.assert_denied(session, route)
        parsed = urlsplit(session.url)
        with self.assertRaises(HTTPError):
            urlopen('http://{}/s/not-the-token/original/0'.format(parsed.netloc), timeout=3)

    def test_zip_contains_exact_subset_unmodified_and_stored(self):
        session = self.start()
        with self.get(session, 'download?ids=1') as response:
            data = response.read()
        with zipfile.ZipFile(BytesIO(data)) as archive:
            self.assertEqual(archive.namelist(), ['JC_test_1.JPG'])
            self.assertEqual(archive.read('JC_test_1.JPG'), self.paths[1].read_bytes())
            self.assertEqual(archive.infolist()[0].compress_type, zipfile.ZIP_STORED)
        with self.get(session, 'download?ids=0&ids=1') as response:
            with zipfile.ZipFile(BytesIO(response.read())) as archive:
                self.assertEqual(set(archive.namelist()), {'JC_test_0.JPG', 'JC_test_1.JPG'})

    def test_zip_rejects_empty_bad_duplicate_or_unselected_ids(self):
        session = self.start()
        for query in ('', '?ids=', '?ids=2', '?ids=-1', '?ids=0&ids=0', '?ids=0,1',
                      '?ids=0&x=1', '?ids=%30'):
            with self.subTest(query=query):
                self.assert_denied(session, 'download' + query)
        self.assertEqual(session.status()['downloads_started'], 0)

    @unittest.skipUnless(shutil.which('node'), 'Node required for real page JavaScript regression')
    def test_phone_page_recovers_failed_poll_and_stops_only_for_confirmed_expiry(self):
        # Execute the production page script. The fixture supplies only browser
        # boundaries (DOM/timers/fetch); state changes remain production behavior.
        harness = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(0,'utf8');
function fixture(seconds=60){
 const boxes=[{checked:true}], intervals=new Map(), timeouts=new Map();
 const link={href:'original/0',removeAttribute(k){delete this[k];},setAttribute(k,v){this[k]=v;}};
 const elements={photos:{querySelectorAll(){return boxes;},addEventListener(){}},
 count:{textContent:''},download:{disabled:false},state:{textContent:''}};
 boxes[0].addEventListener=()=>{};
 const context={document:{body:{dataset:{seconds:String(seconds)}},getElementById(k){return elements[k];},querySelectorAll(){return [link];}},
 setInterval(fn,ms){intervals.set(ms,fn);},setTimeout(fn,ms){const id={};timeouts.set(id,fn);return id;},
 clearTimeout(id){timeouts.delete(id);},AbortController,fetch:()=>Promise.reject(Error('temporary'))};
 vm.runInNewContext(source,context);
 return {context,elements,link,intervals,timeouts};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 const f=fixture();let calls=0;
 f.context.fetch=()=>{calls++;return Promise.reject(Error('busy'));};
 f.intervals.get(5000)();await settle();await settle();
 assert.equal(f.elements.download.disabled,false,'temporary failed status poll disabled active downloads');
 assert.equal(f.link.href,'original/0','temporary failed poll destroyed original link');
 f.context.fetch=()=>{calls++;return Promise.resolve({ok:true,json:async()=>({active:true,seconds_left:55})});};
 f.intervals.get(5000)();await settle();await settle();
 assert.equal(calls,2,'page never retried a temporarily rejected status poll');
 assert.equal(f.elements.download.disabled,false);assert.equal(f.link.href,'original/0');
 assert(!f.elements.state.textContent.includes('暂时'),'recovered page still reports temporary disconnect');
 let finish;
 f.context.fetch=()=>{calls++;return new Promise(resolve=>{finish=resolve;});};
 f.intervals.get(5000)();f.intervals.get(5000)();await settle();
 assert.equal(calls,3,'overlapping status polls were not bounded');
 finish({ok:true,json:async()=>({active:false,seconds_left:0})});await settle();await settle();
 assert.equal(f.elements.download.disabled,true,'confirmed close left ZIP enabled');
 assert.equal(f.link.href,undefined,'confirmed close left original link enabled');
 f.intervals.get(5000)();await settle();assert.equal(calls,3);
 const expired=fixture(1);expired.intervals.get(1000)();
 assert.equal(expired.elements.download.disabled,true,'local TTL expiry left ZIP enabled');
 assert.equal(expired.link.href,undefined,'local TTL expiry left original link enabled');
 console.log('page JS: transient rejection -> recovery; one inflight poll; confirmed/local expiry: PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        result = subprocess.run([shutil.which('node'), '-e', harness], input=share_page.SCRIPT,
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_phone_page_and_thumbnail_offer_only_selected_files(self):
        session = self.start()
        with self.get(session) as response:
            page = response.read().decode('utf-8')
        self.assertIn('JC_test_0.JPG', page)
        self.assertIn('JC_test_1.JPG', page)
        self.assertNotIn('JC_test_2.JPG', page)
        self.assertIn('文件', page)
        self.assertIn('ZIP', page)
        self.assertIn('checked', page)
        with self.get(session, 'thumbnail/0') as response:
            with Image.open(BytesIO(response.read())) as thumbnail:
                self.assertEqual(thumbnail.format, 'JPEG')
                self.assertLessEqual(max(thumbnail.size), 320)
        self.assertEqual(session.status()['downloads_started'], 0)

    def test_host_origin_and_body_restrictions(self):
        session = self.start()
        self.assert_denied(session, 'original/0', {'Host': 'attacker.invalid'}, 403)
        self.assert_denied(session, 'original/0', {'Origin': 'https://attacker.invalid'}, 403)
        self.assert_denied(session, 'original/0', {'Origin': 'null'}, 403)
        self.assert_denied(session, 'original/0', {'Content-Length': '10'}, 400)
        self.assert_denied(session, 'original/0', {'Transfer-Encoding': 'chunked'}, 400)
        with self.get(session, 'original/0', {'Origin': session.url.split('/s/')[0]}) as response:
            self.assertEqual(response.read(), self.paths[0].read_bytes())
        self.assertEqual(session.status()['downloads_started'], 1)

    def test_oversized_headers_queries_and_post_are_rejected(self):
        session = self.start()
        self.assert_denied(session, 'original/0', {'X-Oversized': 'x' * 20000}, 431)
        self.assert_denied(session, 'download?ids=' + '0' * 10000, expected=414)
        with self.assertRaises(HTTPError) as raised:
            urlopen(Request(session.url, data=b'upload', method='POST'), timeout=3)
        self.assertEqual(raised.exception.code, 405)
        self.assertEqual(session.status()['downloads_started'], 0)

    def test_replaced_or_mutated_selected_file_is_rejected_before_headers(self):
        session = self.start()
        self.paths[0].write_bytes(self.paths[2].read_bytes())
        self.assert_denied(session, 'original/0', expected=409)
        self.assert_denied(session, 'download?ids=0&ids=1', expected=409)
        self.assertEqual(session.status()['downloads_started'], 0)

    def test_symlink_swap_after_start_never_leaks_target(self):
        session = self.start()
        moved = self.photos / 'retained-original.JPG'
        self.paths[0].rename(moved)
        self.paths[0].symlink_to(self.paths[2])
        self.assert_denied(session, 'original/0', expected=409)
        self.assert_denied(session, 'thumbnail/0', expected=409)

    def test_start_rejects_invalid_selection(self):
        invalid = self.photos / 'JC_bad.JPG'
        invalid.write_bytes(b'not a jpeg')
        outside = self.directory / 'JC_outside.JPG'
        outside.write_bytes(self.paths[0].read_bytes())
        linked = self.photos / 'JC_link.JPG'
        linked.symlink_to(self.paths[0])
        for paths in ([], [self.paths[0], self.paths[0]], [outside], [linked], [invalid],
                      [self.photos / 'JC_missing.JPG']):
            with self.subTest(paths=paths):
                with self.assertRaises((ValueError, OSError)):
                    self.start(paths)

    def test_camera_names_cannot_be_archive_paths(self):
        # A POSIX backslash filename can become a path separator in phone/PC ZIP tools.
        unsafe = self.photos / 'JC_folder\\escape.JPG'
        unsafe.write_bytes(self.paths[0].read_bytes())
        with self.assertRaises(ValueError):
            self.start([unsafe])

    def test_limits_reject_over_count_and_sparse_over_size_before_decode(self):
        with self.assertRaises(ValueError):
            self.start([self.paths[0]] * 101)
        huge = self.photos / 'JC_huge.JPG'
        with huge.open('wb') as output:
            output.truncate(2 * 1024 ** 3 + 1)
        with self.assertRaises(ValueError):
            self.start([huge])

    def test_public_wildcard_and_invalid_addresses_are_rejected(self):
        for address in ('0.0.0.0', '8.8.8.8', '::1', 'localhost', '169.254.1.2'):
            with self.subTest(address=address):
                with self.assertRaises(ValueError):
                    PhotoShareSession.start(self.photos, self.paths[:1], address)

    def test_close_is_prompt_idempotent_and_revokes_idle_connections(self):
        session = self.start()
        endpoint = urlsplit(session.url)
        idle = socket.create_connection((endpoint.hostname, endpoint.port), timeout=3)
        idle.sendall(b'GET ')
        time.sleep(.05)
        before = time.monotonic()
        session.close()
        session.close()
        self.assertLess(time.monotonic() - before, 1)
        self.assertFalse(session.status()['active'])
        try:
            self.assertEqual(idle.recv(1), b'')
        except ConnectionResetError:
            pass
        finally:
            idle.close()
        with self.assertRaises((OSError, HTTPError)):
            self.get(session, 'original/0')

    def test_slow_validation_does_not_consume_published_session_ttl(self):
        real_monotonic = time.monotonic
        real_open = Image.open
        elapsed = [0]

        def slow_open(*args, **kwargs):
            image = real_open(*args, **kwargs)
            elapsed[0] += 2
            return image

        # Two valid JPEG validations take four logical seconds, exceeding TTL=1.
        with patch.object(photo_share.time, 'monotonic', lambda: real_monotonic() + elapsed[0]), \
                patch.object(photo_share.Image, 'open', slow_open):
            session = self.start(ttl_seconds=1)
            self.assertTrue(session.status()['active'])
            self.assertEqual(session.status()['seconds_left'], 1)
            with self.get(session, 'original/1') as response:
                self.assertEqual(response.read(), self.paths[1].read_bytes())

    def test_expiry_revokes_without_a_gui_poll(self):
        session = self.start(ttl_seconds=1)
        time.sleep(1.2)
        self.assertFalse(session.status()['active'])
        self.assertEqual(session.status()['seconds_left'], 0)
        with self.assertRaises((OSError, HTTPError)):
            self.get(session, 'original/0')

    def test_close_cancels_stalled_download_without_claiming_it_finished(self):
        # JPEG permits trailing bytes; retain an 8 MiB real file to block socket writes.
        with self.paths[0].open('ab') as output:
            for _ in range(128):
                output.write(b'x' * 65536)
        session = self.start(self.paths[:1])
        endpoint = urlsplit(session.url)
        client = socket.create_connection((endpoint.hostname, endpoint.port), timeout=3)
        try:
            client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            client.sendall(('GET ' + endpoint.path + 'original/0 HTTP/1.0\r\nHost: '
                            + endpoint.netloc + '\r\n\r\n').encode())
            self.assertIn(b'200 OK', client.recv(1024))
            time.sleep(.1)
            self.assertEqual(session.status()['downloads_started'], 1)
            before = time.monotonic()
            session.close()
            self.assertLess(time.monotonic() - before, 1)
            self.assertEqual(session.status()['downloads_finished'], 0)
            self.assertEqual(session.status()['active_requests'], 0)
        finally:
            client.close()

    def test_aggregate_size_and_invalid_ttl_are_rejected(self):
        for path in self.paths[:2]:
            with path.open('wb') as output:
                output.truncate(1024 ** 3 + 1)
        with self.assertRaises(ValueError):
            self.start()
        for ttl in (0, 901, -1, 1.5, True):
            with self.subTest(ttl=ttl):
                with self.assertRaises(ValueError):
                    self.start(self.paths[2:], ttl_seconds=ttl)

    def test_aggregate_headers_and_duplicate_host_are_rejected(self):
        session = self.start()
        headers = {'X-Test-{}'.format(index): 'a' * 1000 for index in range(10)}
        self.assert_denied(session, 'original/0', headers, 431)
        endpoint = urlsplit(session.url)
        connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=3)
        try:
            connection.putrequest('GET', endpoint.path + 'original/0')
            connection.putheader('Host', endpoint.netloc)
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
        finally:
            connection.close()

    def test_names_are_escaped_and_never_interpreted_as_page_template(self):
        name = self.photos / 'JC_SCRIPT_照片<unsafe>.JPG'
        name.write_bytes(self.paths[0].read_bytes())
        session = self.start([name])
        with self.get(session) as response:
            page = response.read().decode()
        self.assertIn('JC_SCRIPT_照片&lt;unsafe&gt;.JPG', page)
        self.assertNotIn('<unsafe>', page)
        with self.get(session, 'original/0') as response:
            self.assertIn('UTF-8', response.headers['Content-Disposition'])
            self.assertEqual(response.read(), name.read_bytes())

    def test_slow_trickle_cannot_hold_workers_past_absolute_acquisition_deadline(self):
        # Shorten only the acquisition deadline; byte gaps remain far below idle=5s.
        with patch.object(photo_share, 'HEADER_TIMEOUT', .5, create=True):
            session = self.start()
            endpoint = urlsplit(session.url)
            clients = [socket.create_connection((endpoint.hostname, endpoint.port), timeout=2)
                       for _ in range(4)]
            try:
                for client in clients:
                    client.sendall(b'GET ')
                started = time.monotonic()
                advanced_to_headers = False
                while time.monotonic() - started < .68:
                    elapsed = time.monotonic() - started
                    for index, client in enumerate(clients):
                        try:
                            # Two peers never finish the request line. Two spend
                            # most of the budget on it, then trickle an incomplete header.
                            # At .68s a reset header deadline (.3+.5) would still
                            # hold them, while the shared .5s deadline has elapsed.
                            if index < 2 and elapsed > .3 and not advanced_to_headers:
                                client.sendall((endpoint.path + ' HTTP/1.0\r\nHost: ').encode())
                            elif index >= 2 or elapsed > .3:
                                client.sendall(b'x')
                        except OSError:
                            pass
                    if elapsed > .3:
                        advanced_to_headers = True
                    time.sleep(.03)
                self.assertEqual(session.status()['active_requests'], 0,
                                 'trickled bytes kept unauthenticated workers occupied')
                with self.get(session, 'original/0') as response:
                    self.assertEqual(response.read(), self.paths[0].read_bytes())
            finally:
                for client in clients:
                    client.close()

    def test_idle_client_flood_keeps_worker_count_bounded(self):
        session = self.start()
        endpoint = urlsplit(session.url)
        clients = []
        for _ in range(12):
            try:
                client = socket.create_connection((endpoint.hostname, endpoint.port), timeout=.3)
                client.sendall(b'GET ')
                clients.append(client)
            except OSError:
                pass
        time.sleep(.1)
        self.assertLessEqual(session.status()['active_requests'], 4)
        self.assertGreater(session.status()['active_requests'], 0)
        before = time.monotonic()
        session.close()
        self.assertLess(time.monotonic() - before, 1)
        for client in clients:
            client.close()


if __name__ == '__main__':
    unittest.main()
