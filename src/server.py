
from concurrent import futures
import logging
import queue

import time
from datetime import datetime
import secrets

import grpc
import town_of_salem_pb2
import town_of_salem_pb2_grpc

import random

class Player:
    def __init__(self, name, password):
        self.name = name
        self.password = password
        self.role = "none"
        self.message_queue = queue.Queue() 

class HelloService(town_of_salem_pb2_grpc.HelloServiceServicer):
    def __init__(self):
        self.users = {}

    def RegisterPlayer(self, request, context):
        user_id = secrets.token_hex(16)
        self.users[user_id] = Player(request.username, request.password)
        logging.info("Registered Player: %s with id %s", request.username, user_id)
        return town_of_salem_pb2.LoginResponse(code=0, user_id=user_id)
    
    def ReceiveMessages(self, request, context):
        message_queue = self.users[request.user_id].message_queue
        logging.info("User id %s is listening for messages", request.user_id)
        
        try:
            # Keep the stream open and yield messages as they arrive in the queue
            while context.is_active():
                try:
                    # Non-blocking timeout allows checking context.is_active() periodically
                    message = message_queue.get(timeout=1.0)
                    yield message
                except queue.Empty:
                    continue
        finally:
            # Clean up the registry when the client disconnects or cancels
            if self.users[request.user_id]:
                del self.users[request.user_id]
                logging.info("User id %s is disconnected", request.user_id)

    def SendMessage(self, request, context):
        # Check state of game
        sendname = self.users[request.user_id].name
        sendtext = request.text
        receivers = "all"

        # Process message if it's a command
        if sendtext.startswith("/"):
            arguments = request.text.split(" ")
            if arguments[0] == "/message":
                if len(arguments) < 2:
                    sendname = "Server"
                    sendtext = "error - message has invalid receiver"
                    receivers = request.user_id
                else:
                    sendtext = " ".join(arguments[2:])
                    for user_id, user in self.users.items():
                        if user.name == arguments[1]:
                            receivers = [request.user_id, user_id]
            if arguments[0] == "/role":
                sendname = "Server"
                sendtext = self.users[request.user_id].role
                receivers = request.user_id
            if arguments[0] == "/host":
                sendname = "Server"
                sendtext = "Starting Game!"
                self.start_game(request)

        # Format message
        username = sendname
        clock_time = datetime.fromtimestamp(request.timestamp).strftime("%H:%M:%S")
        sendtext = f"{clock_time} {username}: {sendtext}"
        if sendname == "Server":
            sendtext = f"\033[33m{sendtext}\033[0m"
        elif receivers == "all":
            sendtext = f"\033[32m{sendtext}\033[0m"
        elif receivers == "werewolves":
            sendtext = f"\033[31m{sendtext}\033[0m"
        else:
            sendtext = f"\033[34m{sendtext}\033[0m"
        request.text = sendtext

        # Determine who will receive the message
        if receivers == "all":
            for user in list(self.users.values()):
                user.message_queue.put(request)
        elif receivers == "werewolves":
            for user in list(self.users.values()):
                if user.role == "werewolf":
                    user.message_queue.put(request)
        elif (not isinstance(receivers, list)):
            self.users[receivers].message_queue.put(request)
        else:
            for receiver in receivers:
                self.users[receiver].message_queue.put(request)


        return town_of_salem_pb2.Empty()

    def start_game(self, request):
        werewolf_starting_amount = 1
        count = 0
        for user in self.users.values():
            if count < werewolf_starting_amount:
                user.role = "werewolf"
                count += 1
            else:
                user.role = "town"
            
            request.text = f"\033[33mYou got the role {user.role}\033[0m"
            user.message_queue.put(request)

def serve():
    uds_address = "unix:helloworld.sock"
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    town_of_salem_pb2_grpc.add_HelloServiceServicer_to_server(HelloService(), server)
    server.add_insecure_port(uds_address)
    logging.info("Server listening on: %s", uds_address)
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    serve()
