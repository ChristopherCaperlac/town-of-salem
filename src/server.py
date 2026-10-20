
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

import threading

class Player:
    def __init__(self, name, password):
        self.name = name
        self.password = password
        self.role = "none"
        self.message_queue = queue.Queue()
        self.votes = 0
        self.hasVoted = False
        self.hasInvestigated = False

class HelloService(town_of_salem_pb2_grpc.HelloServiceServicer):
    def __init__(self):
        self.users = {}
        self.server_id = secrets.token_hex(16)
        logging.info("Registered Server with id %s", self.server_id)
        self.users[self.server_id] = Player("Server", "password")
        self.current_game = False
        self.current_werewolf_count = 0
        self.werewolf_starting_amount = 1
        self.investigator_starting_amount = 1
        self.isDay = True
        self.isVoting = False

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
            while context.is_active() and request.user_id in self.users.keys():
                try:
                    # Non-blocking timeout allows checking context.is_active() periodically
                    message = message_queue.get(timeout=1.0)
                    yield message
                except queue.Empty:
                    continue
        finally:
            # Clean up the registry when the client disconnects or cancels
            if request.user_id in self.users.keys():
                del self.users[request.user_id]
                logging.info("User id %s is disconnected", request.user_id)
        
    def SendMessage(self, request, context):
        # Check state of game
        sendname = self.users[request.user_id].name
        sendtext = request.text
        receivers = "all"
        if not self.isDay:
            receivers = "none"
            if self.users[request.user_id].role == "werewolf":
                receivers = "werewolves"

        # Process message if it's a command
        if sendtext.startswith("/"):
            arguments = request.text.split(" ")
            if arguments[0] == "/message":
                if len(arguments) < 2 or not self.isDay:
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
            if arguments[0] == "/vote":
                if (len(arguments) < 2) or (not self.isVoting) or (not self.isDay and self.users[request.user_id].role.startswith("town")):
                    sendname = "Server"
                    sendtext = "error - invalid vote"
                    receivers = request.user_id
                else:
                    vote_id = None
                    for user_id, user in self.users.items():
                        if user.name == arguments[1]:
                            vote_id = user_id
                    if not vote_id:
                        sendname = "Server"
                        sendtext = "error - invalid person"
                        receivers = request.user_id
                    else:
                        self.users[vote_id].votes += 1
                        self.users[request.user_id].hasVoted = True

                        sendtext = f"{sendname} has voted for {arguments[1]}. They have {self.users[vote_id].votes} votes"
                        sendname = "Server"

            if arguments[0] == "/investigate":
                if (len(arguments) < 2) or self.isDay or (not (self.users[request.user_id].role == ("town investigator"))):
                    sendname = "Server"
                    sendtext = "error - invalid command"
                    receivers = request.user_id
                elif self.users[request.user_id].hasInvestigated:
                    sendname = "Server"
                    sendtext = "error - already investigated"
                    receivers = request.user_id
                else:
                    invest_id = None
                    for user_id, user in self.users.items():
                        if user.name == arguments[1]:
                            invest_id = user_id
                    if not invest_id:
                        sendname = "Server"
                        sendtext = "error - invalid person"
                        receivers = request.user_id
                    else:
                        invest_role = self.users[invest_id].role
                        self.users[request.user_id].hasInvestigated = True
                        sendname = "Server"
                        sus = " not " if invest_role.startswith("town") else " "
                        sendtext = f"{arguments[1]} is{sus}suspicious"
                        receivers = request.user_id
                    

            if arguments[0] == "/host":
                sendname = "Server"
                if self.current_game:
                    sendtext = "error - there is already a game"
                    receivers = request.user_id
                else:
                    self.current_game = True
                    sendtext = "Starting Game!"
                    self.start_game(request)

        # Format message
        username = sendname
        clock_time = datetime.fromtimestamp(request.timestamp).strftime("%H:%M:%S")
        sendtext = f"{clock_time} {username}: {sendtext}"
        if request.user_id == self.server_id or sendname == "Server":
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
        elif receivers == "none":
            return town_of_salem_pb2.Empty()
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
        self.current_werewolf_count = 0
        current_investigator_count = 0

        shuffled_user_ids = random.sample(list(self.users.keys()), k=len(self.users))
        for user_id in shuffled_user_ids:
            user = self.users[user_id]
            if user_id == self.server_id:
                continue
            if self.current_werewolf_count < self.werewolf_starting_amount:
                user.role = "werewolf"
                self.current_werewolf_count += 1
            else:
                if current_investigator_count < self.investigator_starting_amount:
                    user.role = "town investigator"
                    current_investigator_count += 1
                else:
                    user.role = "town normal"
            
            text = f"\033[33mYou got the role {user.role}\033[0m"
            chatmessage = town_of_salem_pb2.ChatMessage(user_id=user_id, timestamp=int(time.time()), text=text)
            self.users[user_id].message_queue.put(chatmessage)

        threading.Thread(target=self.run_game, args=(), daemon=True).start()

    def run_game(self):
        day_count = 0
        night_count = 0
        dead_username = None
        time.sleep(1)

        while True:
            # Day Cycle
            self.isDay = True
            day_count += 1
            text = f"Day {day_count}"
            text = f"\033[33m{text}\033[0m"
            chatmessage = town_of_salem_pb2.ChatMessage(user_id=self.server_id, timestamp=int(time.time()), text=text)
            for user in list(self.users.values()):
                user.message_queue.put(chatmessage)

            for user_id, user in list(self.users.items()):
                if user.votes == self.current_werewolf_count:
                    dead_username = user.name
                    dead_role = user.role
                    if dead_role == "werewolf":
                        self.current_werewolf_count -= 1
                    text = f"You have been killed (Restart Client)"
                    text = f"\033[33m{text}\033[0m"
                    chatmessage = town_of_salem_pb2.ChatMessage(user_id=self.server_id, timestamp=int(time.time()), text=text)
                    self.users[user_id].message_queue.put(chatmessage)
                    time.sleep(0.2)
                    del self.users[user_id]
            if dead_username:
                text = f"{dead_username} has been killed. They had a role of {dead_role}"
                text = f"\033[33m{text}\033[0m"
                chatmessage = town_of_salem_pb2.ChatMessage(user_id=self.server_id, timestamp=int(time.time()), text=text)
                for user in list(self.users.values()):
                    user.message_queue.put(chatmessage)
            dead_username = None
            if ((len(self.users) - 1) == self.current_werewolf_count) or self.current_werewolf_count == 0:
                break
            time.sleep(0.5)

            # Discussion
            self.isVoting = False
            text = f"Discussion (10 seconds)"
            text = f"\033[33m{text}\033[0m"
            chatmessage = town_of_salem_pb2.ChatMessage(user_id=self.server_id, timestamp=int(time.time()), text=text)
            for user in list(self.users.values()):
                user.message_queue.put(chatmessage)
            time.sleep(10)
            self.isVoting = True

            # Voting
            text = f"Voting (25 seconds)"
            text = f"\033[33m{text}\033[0m"
            chatmessage = town_of_salem_pb2.ChatMessage(user_id=self.server_id, timestamp=int(time.time()), text=text)
            for user in list(self.users.values()):
                user.message_queue.put(chatmessage)
            
            for user in list(self.users.values()):
                user.votes = 0
                user.hasVoted = False 
            time.sleep(25)
            player_count = len(self.users) - 1
            for user_id, user in list(self.users.items()):
                if user.votes > (player_count / 2):
                    dead_username = user.name
                    dead_role = user.role
                    if dead_role == "werewolf":
                        self.current_werewolf_count -= 1
                    text = f"You have been killed (Restart Client)"
                    text = f"\033[33m{text}\033[0m"
                    chatmessage = town_of_salem_pb2.ChatMessage(user_id=self.server_id, timestamp=int(time.time()), text=text)
                    self.users[user_id].message_queue.put(chatmessage)
                    time.sleep(0.2)
                    del self.users[user_id]
            if dead_username:
                text = f"{dead_username} has been hanged. They had a role of {dead_role}"
                text = f"\033[33m{text}\033[0m"
                chatmessage = town_of_salem_pb2.ChatMessage(user_id=self.server_id, timestamp=int(time.time()), text=text)
                for user in list(self.users.values()):
                    user.message_queue.put(chatmessage)
            dead_username = None
            if ((len(self.users) - 1) == self.current_werewolf_count) or self.current_werewolf_count == 0:
                break
            time.sleep(0.5)

            # Night Cycle
            self.isDay = False
            night_count += 1
            text = f"Night {night_count} (30 seconds)"
            text = f"\033[33m{text}\033[0m"
            chatmessage = town_of_salem_pb2.ChatMessage(user_id=self.server_id, timestamp=int(time.time()), text=text)
            for user in list(self.users.values()):
                user.message_queue.put(chatmessage)

            for user in list(self.users.values()):
                user.votes = 0
                user.hasVoted = False 
            time.sleep(30)
            for user in list(self.users.values()):
                user.hasInvestigated = False 

        if (len(self.users) - 1) == self.current_werewolf_count:
            text = f"Werewolves Win!"
            text = f"\033[33m{text}\033[0m"
            chatmessage = town_of_salem_pb2.ChatMessage(user_id=self.server_id, timestamp=int(time.time()), text=text)
            for user in list(self.users.values()):
                user.message_queue.put(chatmessage)
        elif self.current_werewolf_count == 0:
            text = f"Town Win!"
            text = f"\033[33m{text}\033[0m"
            chatmessage = town_of_salem_pb2.ChatMessage(user_id=self.server_id, timestamp=int(time.time()), text=text)
            for user in list(self.users.values()):
                user.message_queue.put(chatmessage)
        self.current_game = False

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
