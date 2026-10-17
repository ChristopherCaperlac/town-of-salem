from __future__ import print_function
import logging
import time

import threading
import sys

import grpc
import town_of_salem_pb2
import town_of_salem_pb2_grpc

def listen_for_messages(stub, user_id):
    try:
        chatMessages = stub.ReceiveMessages(town_of_salem_pb2.AuthInfo(user_id=user_id))
        for chatMessage in chatMessages:
            print(f"\r{chatMessage.text}")
            print("\r> ", end="", flush=True)
    except grpc.RpcError as e:
        print(f"\nDisconnected from server: {e.details()}")

def run():
    uds_address = "unix:helloworld.sock"
    with grpc.insecure_channel(uds_address) as channel:
        stub = town_of_salem_pb2_grpc.HelloServiceStub(channel)
        username = input("What is your username? ")
        password = input("What is your password? ")
        response = stub.RegisterPlayer(town_of_salem_pb2.LoginInfo(username=username, password=password))
        if response.code > 0:
            print("Failed to connect\n")
            return
        print("Successfully connected to server")
        user_id = response.user_id

        # Listen for incoming messages
        threading.Thread(target=listen_for_messages, args=(stub, user_id), daemon=True).start()
        
        # Listen for user input and send to server
        try:
            while True:
                text = input("\r> ")
                if text.strip():
                    stub.SendMessage(town_of_salem_pb2.ChatMessage(user_id=user_id, timestamp=int(time.time()), text=text))
        except (KeyboardInterrupt, SystemExit):
            print("\nExiting...")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
