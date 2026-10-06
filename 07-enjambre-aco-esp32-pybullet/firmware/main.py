"""Guardar en la raiz del ESP32 junto con los otros cinco archivos y config.py."""
import time
import gc
import network
import machine
import config
from world import MAP_ID
from agent import Agent
from transport import Transport


def setup_wifi():
    ap_id = getattr(network, "AP_IF", None)
    sta_id = getattr(network, "STA_IF", None)
    if ap_id is None:
        ap_id = network.WLAN.IF_AP
        sta_id = network.WLAN.IF_STA
    wlan = network.WLAN(ap_id if config.NODE_ID == 1 else sta_id)
    wlan.active(True)
    if config.NODE_ID == 1:
        # ESP32 MicroPython: WPA2=3. Los nombres ssid/security/key son actuales;
        # essid/authmode/password mantienen compatibilidad con firmware previo.
        try:
            wlan.config(ssid=config.SSID, security=3, key=config.PASSWORD, max_clients=4)
        except (ValueError, TypeError):
            wlan.config(essid=config.SSID, authmode=3, password=config.PASSWORD, max_clients=4)
        wlan.ifconfig((config.HUB_IP, "255.255.255.0", config.HUB_IP, config.HUB_IP))
        print("AP listo:", config.SSID, wlan.ifconfig())
    else:
        print("Conectando nodo", config.NODE_ID, "a", config.SSID)
        wlan.connect(config.SSID, config.PASSWORD)
        start = time.ticks_ms()
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), start) > 20000:
                raise OSError("No se encontro el AP. Enciende primero el nodo 1.")
            time.sleep_ms(100)
        print("WiFi listo:", wlan.ifconfig())
    return wlan


def run():
    if config.NODE_ID not in (1, 2, 3):
        raise ValueError("NODE_ID debe ser 1, 2 o 3")
    wlan = setup_wifi()
    seed = int.from_bytes(machine.unique_id()[-4:], "big") ^ time.ticks_ms() ^ config.NODE_ID
    boot = "%x-%x" % (seed & 0xffffffff, time.ticks_ms())
    agent = Agent(config.NODE_ID, boot, seed,
                  config.MAX_ITERATIONS, config.ITERATION_MS,
                  config.EDGE_MS, config.SHARE_EVERY)
    link = Transport(config.NODE_ID, boot, config.UDP_PORT, (config.HUB_IP, config.UDP_PORT))
    led = machine.Pin(config.LED_PIN, machine.Pin.OUT) if config.LED_PIN is not None else None
    previous = time.ticks_ms()
    uptime = 0
    hello_at = -1000
    state_at = -200
    gc_at = 0
    previous_phase = ""
    print("READY ACO nodo", config.NODE_ID, "- abre el panel y pulsa Iniciar")
    print("MAPA", MAP_ID, "- alternativas ACO;", config.MAX_ITERATIONS, "iteraciones")
    try:
        while True:
            current = time.ticks_ms()
            dt = max(0, time.ticks_diff(current, previous))
            previous = current
            uptime += dt  # evita problemas de wrap de ticks_ms en el transporte
            if config.NODE_ID != 1 and not wlan.isconnected():
                agent.running = False
                if led:
                    led.value(0)
                print("WiFi desconectado; reinicio para reconectar")
                time.sleep_ms(1000)
                machine.reset()
            if uptime - hello_at >= 1000:
                link.hello(uptime)
                hello_at = uptime
            for packet in link.receive(uptime):
                for reply in agent.on_packet(packet):
                    link.publish(reply, uptime)
            for packet in agent.advance(dt):
                link.publish(packet, uptime)
            if uptime - state_at >= 200:
                state = agent.state()
                state["net_errors"] = link.errors
                link.publish(state, uptime)
                state_at = uptime
            if led:
                led.value(1 if agent.running else 0)
            if agent.phase != previous_phase:
                print("Nodo", config.NODE_ID, agent.phase, "mejor:",
                      len(agent.colony.best_path) - 1 if agent.colony.best_path else "--")
                previous_phase = agent.phase
            if uptime - gc_at >= 2000:
                gc.collect()
                gc_at = uptime
            time.sleep_ms(10)
    finally:
        link.close()
        if led:
            led.value(0)


run()
