import socket
import platform
import uuid
from collectors.dbprobe.detect import detect_engines
from collectors.webprobe.detect import detect_servers
from utils.command_registry import get_handler , get_status , set_status , register_thread , remove_thread
from collectors.flyprobe.detect import detect_fly
from collectors.appprobe.detect import detect_appservers

import os
def get_mac_address() -> str:
    """
    Extracts and formats the hardware MAC address of the primary network interface.
    """
    # uuid.getnode() fetches a 48-bit integer representing the hardware address
    mac_num = uuid.getnode()
    # Format the integer into standard 12-character hex pairs separated by colons
    mac_str = ':'.join(['{:02x}'.format((mac_num >> ele) & 0xff) for ele in range(0, 8*6, 8)][::-1])
    return mac_str

def get_machine_info() -> dict:
    """
    Gathers the primary local IPv4 address, hostname, OS details, 
    and current hardware resource utilization.
    """
    main_ipv4 = "127.0.0.1"
    hostname = socket.gethostname()
    
    # Extract the main active local IPv4 address
    try:
        # We connect to a public DNS IP (does not actually send any packets)
        # to force the OS to pick the interface facing the internet/router.
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        main_ipv4 = s.getsockname()[0]
        s.close()
    except Exception:
        # Fallback if the machine is entirely offline
        try:
            main_ipv4 = socket.gethostbyname(hostname)
        except Exception:
            pass

    # Gather underlying system architecture details
    info = {
        "mac_address": get_mac_address(),
        "host_name": hostname,
        "main_ip": main_ipv4,
        "all_ips": [ip[4][0] for ip in socket.getaddrinfo(hostname, None) if ip[4][0]],
        "os": platform.system().lower(),
        "release": platform.release(),
        "version": platform.version(),
        "machine_architecture": platform.machine()
    }
    return info



async def handle_command(payload):

    command = payload.get("command")
    args = payload.get("args")

    if command == "active_test":
        status = get_status()
        return {"success" : True , "status" : status}

    if command == "update_status":
        status = set_status(args.get("status"))
        return {"success": True , "status" : status}

    
    if command ==  "list_services":
        det=[]
        det=(detect_engines()+detect_servers()+detect_fly()+detect_appservers())
        return det
    
    inspector = get_handler("engines_handler")
    web_inspector=get_handler("web_inspector")
    fly_inspector=get_handler("fly_inspector")
    App_inspector=get_handler("Appserver_inspector")


    if inspector is not None:
        service_name = args.get("service_name")

        if command == "start_engine":
            ins = inspector
            return register_thread(service_name , ins , args)
            
        if command == "stop_engine":
            return remove_thread(service_name)


    if web_inspector is not None:
        service_name = args.get("service_name")

        if command == "start_web":  
            w_ins = web_inspector
            return register_thread(service_name , w_ins , args)      
                    
        if command == "stop_web":
            return remove_thread(service_name)
        
    if fly_inspector is not None:
        service_name = args.get("service_name")

        if command == "start_fly":
            f_ins = fly_inspector
            return register_thread(service_name , f_ins , args)
        if command == "stop_fly":
            return remove_thread(service_name)
    
    if App_inspector is not None:
        service_name = args.get("service_name")

        if command == "start_appserver":  
            a_ins = App_inspector

            return register_thread(service_name , a_ins , args)
                    
        if command == "stop_appserver":
            return remove_thread(service_name)
    return []  

if __name__ == "__main__":
    print(get_machine_info())