const WebSocket = require('ws');
const http = require('http');
const express = require('express');

const app = express();
const server = http.createServer(app);
const wss = new WebSocket.Server({ server });

let waitingUser = null;
const rooms = new Map(); // ws -> partner_ws

wss.on('connection', (ws) => {
    console.log('Pengguna terhubung ke signaling server.');

    ws.on('message', (message) => {
        let data;
        try {
            data = JSON.parse(message);
        } catch (e) {
            return;
        }

        switch (data.type) {
            case 'find':
                // Jika ada user sedang menunggu
                if (waitingUser && waitingUser !== ws && waitingUser.readyState === WebSocket.OPEN) {
                    rooms.set(ws, waitingUser);
                    rooms.set(waitingUser, ws);

                    // Beritahu keduanya bahwa partner ditemukan
                    ws.send(JSON.stringify({ type: 'matched', role: 'caller' }));
                    waitingUser.send(JSON.stringify({ type: 'matched', role: 'callee' }));

                    waitingUser = null; // Reset antrean
                } else {
                    waitingUser = ws;
                    ws.send(JSON.stringify({ type: 'waiting' }));
                }
                break;

            case 'next':
                // Putus dari partner lama
                cleanupPartner(ws);
                // Masukkan kembali ke antrean cari partner
                if (waitingUser && waitingUser !== ws && waitingUser.readyState === WebSocket.OPEN) {
                    rooms.set(ws, waitingUser);
                    rooms.set(waitingUser, ws);
                    ws.send(JSON.stringify({ type: 'matched', role: 'caller' }));
                    waitingUser.send(JSON.stringify({ type: 'matched', role: 'callee' }));
                    waitingUser = null;
                } else {
                    waitingUser = ws;
                    ws.send(JSON.stringify({ type: 'waiting' }));
                }
                break;

            case 'offer':
            case 'answer':
            case 'candidate':
                const partner = rooms.get(ws);
                if (partner && partner.readyState === WebSocket.OPEN) {
                    partner.send(JSON.stringify(data));
                }
                break;

            case 'hangup':
                cleanupPartner(ws);
                ws.send(JSON.stringify({ type: 'ended' }));
                break;
        }
    });

    ws.on('close', () => {
        if (waitingUser === ws) {
            waitingUser = null;
        }
        cleanupPartner(ws);
        console.log('Pengguna terputus.');
    });
});

function cleanupPartner(ws) {
    const partner = rooms.get(ws);
    if (partner) {
        if (partner.readyState === WebSocket.OPEN) {
            partner.send(JSON.stringify({ type: 'peer_disconnected' }));
        }
        rooms.delete(partner);
        rooms.delete(ws);
    }
}

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
    console.log(`Signaling server berjalan di port ${PORT}`);
});
