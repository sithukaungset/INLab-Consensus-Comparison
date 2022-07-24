from subprocess import check_output
from Crypto.PublicKey import RSA
from Crypto.Signature import PKCS1_v1_5
from Crypto.Hash import SHA256
from secrets import SystemRandom
from collections import OrderedDict
import ast
import json, pika, threading, pickle
# for creating logs
import logging
# for multi-processing
from multiprocessing import Process, Lock, Manager
import time, base64

global network_nodes, n, s, c, D, r, fin_num, commitmentSet, ledger, port, lock

# n : number of nodes
n = 66
# s - where 2^s is the number of committees
s = 2
# c - size of committee
c = 4
# D - difficulty level , leading bits of PoW must have D 0's (keep w.r.t to hex)
D = 3
# r - number of bits in random string 
r = 5
# fin_num - final committee id
fin_num = 0
# commitmentSet - set of commitments S
commitmentSet = set()
# network_nodes - list of all nodes 
network_nodes = []

# ELASTICO_STATES - states reperesenting the running state of the node
ELASTICO_STATES = {"NONE": 0, "PoW Computed": 1, "Formed Identity" : 2, "Formed Committee": 3, "RunAsDirectory": 4 ,"RunAsDirectory after-TxnReceived" : 5,  "RunAsDirectory after-TxnMulticast" : 6, "Receiving Committee Members" : 7,"PBFT_NONE" : 8 , "PBFT_PRE_PREPARE" : 9, "PBFT_PRE_PREPARE_SENT"  : 10, "PBFT_PREPARE_SENT" : 11, "PBFT_PREPARED" : 12, "PBFT_COMMITTED" : 13, "PBFT_COMMIT_SENT" : 14,  "Intra Consensus Result Sent to Final" : 15,  "Merged Consensus Data" : 16, "FinalPBFT_NONE" : 17,  "FinalPBFT_PRE_PREPARE" : 18, "FinalPBFT_PRE_PREPARE_SENT"  : 19,  "FinalPBFT_PREPARE_SENT" : 20 , "FinalPBFT_PREPARED" : 21, "FinalPBFT_COMMIT_SENT" : 22, "FinalPBFT_COMMITTED" : 23, "PBFT Finished-FinalCommittee" : 24 , "CommitmentSentToFinal" : 25, "FinalBlockSent" : 26, "FinalBlockReceived" : 27,"BroadcastedR" : 28, "ReceivedR" :  29, "FinalBlockSentToClient" : 30,   "LedgerUpdated" : 31}


def consistencyProtocol():
	"""
		Agrees on a single set of Hash values(S)
		presently selecting random c hash of Ris from the total set of commitments
	"""
	global network_nodes, commitmentSet
	# ToDo: implement interactive consistency Protocol
	for node in network_nodes:
		if node.isFinalMember():
			if len(node.commitments) <= c//2:
				logging.warning("insufficientCommitments")
				return False, "insufficientCommitments"

	# ToDo: Discuss with sir about intersection.
	if len(commitmentSet) == 0:
		flag = True
		for node in network_nodes:
			if node.isFinalMember():
				if flag and len(commitmentSet) == 0:
					flag = False
					commitmentSet = node.commitments
				else:
					commitmentSet = commitmentSet.intersection(node.commitments)
	return True,commitmentSet


def random_gen(size=32):
	"""
		generates the size-bit random number
		size denotes the number of bits
	"""
	# with open("/dev/urandom", 'rb') as f:
	#   return int.from_bytes(f.read(4), 'big')
	random_num = SystemRandom().getrandbits(size)
	return random_num


def BroadcastTo_Network(data, type_):
	"""
		Broadcast data to the whole ntw
	"""

	global network_nodes
	# construct msg
	msg = { "data" : data , "type" : type_ }
	# establish a connection with RabbitMQ server
	connection = pika.BlockingConnection(pika.ConnectionParameters(host='localhost'))
	# create a channel
	channel = connection.channel()
	 
	for node in network_nodes:
		try:
			port = node.port
			# create a hello queue to which the message will be delivered
			channel.queue_declare( queue= 'hello' + str(port) )
			
			serialized_data = pickle.dumps(msg)
			channel.basic_publish(exchange='', routing_key='hello' + str(port), body=serialized_data)
		except Exception as e:
			logging.error("error in broadcast to network" , exc_info=e)
			if isinstance(e, ConnectionRefusedError):
				logging.error("ConnectionRefusedError at port : %s", str(node.port))
			raise e
	# close the channel
	channel.close()
	# close the connection
	connection.close()


def MulticastCommittee(commList, identityobj, txns):
	"""
		each node getting views of its committee members from directory members
	"""
	try:
		# get the final committee members with the fixed committee id
		finalCommitteeMembers = commList[fin_num]
		for committee_id in commList:
			# take the committee members of a particular committee id
			commMembers = commList[committee_id]
			# find the primary identity, Take the first identity
			# ToDo: fix this, many nodes can be primary
			primaryId = commMembers[0]
			for memberId in commMembers:
				# send the committee members , final committee members
				data = {"committee members" : commMembers , "final Committee members"  : finalCommitteeMembers , "identity" : identityobj}
				# give txns only to the primary node
				if memberId == primaryId:
					data["txns"] = txns[committee_id]
				# construct the msg
				msg = {"data" : data , "type" : "committee members views"}
				# send the committee member views to nodes
				memberId.send(msg)
	except Exception as e:
		logging.error("error in multicast committees list", exc_info=e)
		raise e


class Identity:
	"""
		class for the identity of nodes
	"""
	def __init__(self, IP, PK, committee_id, PoW, epoch_randomness, port):
		self.IP = IP
		self.PK = PK
		self.committee_id = committee_id
		self.PoW = PoW
		self.epoch_randomness = epoch_randomness
		self.port = port


	def isEqual(self, identityobj):
		"""
			checking two objects of Identity class are equal or not
		"""
		return self.IP == identityobj.IP and self.PK == identityobj.PK and self.committee_id == identityobj.committee_id \
		and self.PoW == identityobj.PoW and self.epoch_randomness == identityobj.epoch_randomness and self.port == identityobj.port


	def send(self, msg):
		"""
			send the msg to node based on their identity
		"""
		try:
			# establish a connection with RabbitMQ server
			connection = pika.BlockingConnection(pika.ConnectionParameters(host='localhost'))
			# create a channel
			channel = connection.channel()

			port = self.port

			# create a hello queue to which the message will be delivered
			channel.queue_declare( queue= 'hello' + str(port) )
			serialized_data = pickle.dumps(msg)
			if channel.basic_publish(exchange='', routing_key='hello' + str(port), body= serialized_data, properties=pika.BasicProperties(delivery_mode=1)):
				pass
			else:
				logging.error("messgae not published %s" , str(msg))
			# close the channel
			channel.close()
			# close the connection
			connection.close()
		except Exception as e:
			logging.error("error at send msg ", exc_info=e)
			raise e


class Transaction:
	"""
		class for transaction
	"""
	def __init__(self, sender, receiver, amount, timestamp):
		self.sender = sender
		self.receiver = receiver
		self.amount = amount
		self.timestamp = timestamp

	def isEqual(self, transaction):
		"""
			compare two objs are equal or not
		"""
		return self.sender == transaction.sender and self.receiver == transaction.receiver and self.amount == transaction.amount and self.timestamp == transaction.timestamp

	def hexdigest(self):
		"""
			Digest of a transaction
		"""
		digest = SHA256.new()
		digest.update(self.sender.encode())
		digest.update(self.receiver.encode())
		digest.update(str(self.amount).encode())
		digest.update(str(self.timestamp).encode())
		return digest.hexdigest()

def txnHexdigest(txnList):
	"""
		return hexdigest for a list of transactions
	"""
	txnList = sorted(txnList, key = lambda txn:txn.timestamp)
	digest = SHA256.new()
	for txn in txnList:
		digest.update( txn.hexdigest().encode() )
	return digest.hexdigest()

class BlockHeader:
	"""
		class for block header
	"""
	def __init__(self, rootHash, prevBlockHash, timestamp, numAncestorBlocks, txnCount):
		self.prevBlockHash = prevBlockHash
		self.timestamp = timestamp
		self.numAncestorBlocks = numAncestorBlocks
		self.txnCount = txnCount
		self.rootHash = rootHash

	def hexdigest(self):
		"""
			Digest of a block header
		"""
		digest = SHA256.new()
		digest.update(self.prevBlockHash.encode())
		digest.update(str(self.timestamp).encode())
		digest.update(str(self.numAncestorBlocks).encode())
		digest.update(str(self.txnCount).encode())
		digest.update(self.rootHash.encode())
		return digest.hexdigest()


class BlockData:
	"""
		class for block data consists of txns and merkle tree
	"""
	def __init__(self, transactions, merkleTree):
		self.transactions = transactions
		self.merkleTree = merkleTree
		self.intraCommitMsgs = []
		self.finalCommitMsgs = []

	def addintraCommitMsg(self, msg):
		"""
			Add commit msgs to block data
		"""
		self.intraCommitMsgs.append(msg)

	def addfinalCommitMsg(self, msg):
		"""
			Add commit msgs to block data
		"""
		self.finalCommitMsgs.append(msg)

	def hexdigest(self):
		"""
			Digest of a block data
		"""
		digest = SHA256.new()
		# take the digest of the txnBlock
		txndigest = txnHexdigest(self.transactions)
		digest.update(txndigest.encode())
		
		# take the digest of the merkle tree
		merkleTreedigest = self.merkleTree.hexdigest()
		digest.update( merkleTreedigest.encode())
		return digest.hexdigest()



class IdentityAndSign:
	"""
		for signature and its identity which can be used for verification
	"""
	def __init__(self, sign, identityobj):
		self.sign = sign
		self.identityobj = identityobj

	def isEqual(self, data):
		"""
			compare two objects of this class
		"""
		return self.sign == data.sign and self.identityobj.isEqual(data.identityobj)

	

class Block:
	"""
		create a block of txns with fields as header , data , identities and signatures
	"""
	def __init__(self, transactions,  prevBlockHash, timestamp, numAncestorBlocks, txnCount, merkleTree):
		rootHash = merkleTree.Get_Root_leaf()
		self.header = BlockHeader(rootHash, prevBlockHash, timestamp, numAncestorBlocks, txnCount)
		self.data = BlockData(transactions, merkleTree)
		self.IdentitiesAndSignatures = []

	def getRootHash(self):
		"""
			get the root hash of the merkle tree
		"""
		return self.header.rootHash

	def addSignAndIdentities(self, data):
		"""
			add signatures and identities for the block
		"""
		verified = True
		if verified:
			for newobj in data:
				# Add the identity and sign only if not present
				flag = False
				for obj in self.IdentitiesAndSignatures:
					if newobj.isEqual(obj):
						flag = True
						break
				if flag == False:
					self.IdentitiesAndSignatures.append(newobj)

	def addintraCommitMsg(self, msg):
		"""
		"""
		self.data.addintraCommitMsg(msg)

	def addfinalCommitMsg(self, msg):
		"""
			Add commit msgs to block data
		"""
		self.data.addfinalCommitMsg(msg)
	

	def verifyBlock(self):
		"""
		"""
		pass

	def hexdigest(self):
		"""
			computes the digest for the block
		"""
		digest = SHA256.new()
		# digest of the block header
		headerDigest = self.header.hexdigest()
		digest.update(headerDigest.encode())
		# digest of the block data
		blockDataDigest = self.data.hexdigest()
		digest.update(blockDataDigest.encode())
		return digest.hexdigest()


class MerkleTree:
	"""
		construct the merkle tree of txns
	"""
	def __init__(self, transactions):
		self.past_transaction = OrderedDict()
		self.listoftransaction = transactions

	def hexdigest(self):
		"""
			hexdigest of the merkle tree
		"""
		digest = SHA256.new()
		# take the digest of the merkle tree contents
		transactionList = self.listoftransaction
		for item in transactionList:
			if type(item) is Transaction:
				txndigest = item.hexdigest()
				digest.update(txndigest.encode())
			else:
				digest.update(str(item).encode())

		dictOfTxns = self.past_transaction
		for key, value in dictOfTxns.items():
			# take the digest of the key 
			if type(key) is Transaction:
				keydigest = key.hexdigest()
				digest.update(keydigest.encode())
			else:
				digest.update(str(key).encode())
			# take the digest of the value 
			digest.update(str(value).encode())
		return digest.hexdigest()


	# Create the Merkle Tree  
	def create_tree(self):

		listoftransaction = self.listoftransaction
		past_transaction = self.past_transaction
		temp_transaction = []

		# Loop until the list finishes
		for index in range(0,len(listoftransaction),2):

			# Get the most left element 
			current = listoftransaction[index]

			# If there is still index left get the right of the left most element
			if index+1 != len(listoftransaction):
				current_right = listoftransaction[index+1]

			# If we reached the limit of the list then make a empty string
			else:
				current_right = ''

			# Apply the Hash 256 function to the current values
			if type(current) is Transaction:
				current_hash = current.hexdigest()
			else:
				current_hash = hexdigest(current) 
			# If the current right hash is not a '' <- empty string
			if current_right != '':
				if type(current_right) is Transaction:
					current_right_hash = current_right.hexdigest()
				else:
					current_right_hash = hexdigest(current_right) 
			# Add the Transaction to the dictionary 
			past_transaction[listoftransaction[index]] = current_hash

			# If the next right is not empty
			if current_right != '':
				past_transaction[listoftransaction[index+1]] = current_right_hash

			# Create the new list of transaction
			if current_right != '':
				temp_transaction.append(current_hash + current_right_hash)

			#  If the left most is an empty string then only add the current value
			else:
				temp_transaction.append(current_hash)

		#  Update the variables and rerun the function again 
		if len(listoftransaction) != 1:
			self.listoftransaction = temp_transaction
			self.past_transaction = past_transaction

			#  Call the function repeatly again and again until we get the root 
			self.create_tree()


	# Return the past Transaction 
	def Get_past_transacion(self):
		return self.past_transaction

	# Get the root of the transaction
	def Get_Root_leaf(self):
		last_value = list(self.past_transaction.values())[-1]
		return last_value



class FinalCommittedBlock:
	"""
		final committed block that consists of txns and list of signatures and identities
	"""
	def __init__(self, txnList, listSignaturesAndIdentityobjs):
		self.txnList = txnList
		self.listSignaturesAndIdentityobjs = listSignaturesAndIdentityobjs


class Elastico:
	"""
		class members: 
			node - single processor
			connection - rabbitmq connection
			IP - IP address of a node
			port - unique number for a process
			key - public key and private key pair for a node
			PoW - dict containing 256 bit hash computed by the node, set of Rs needed for epoch randomness, and a nonce
			cur_directory - list of directory members in view of the node
			identity - identity consists of Public key, an IP, PoW, committee id, epoch randomness, port
			committee_id - integer value to represent the committee to which the node belongs
			committee_list - list of nodes in all committees
			committee_Members - set of committee members in its own committee
			is_directory - whether the node belongs to directory committee or not
			is_final - whether the node belongs to final committee or not
			epoch_randomness - r-bit random string generated at the end of previous epoch
			Ri - r-bit random string
			commitments - set of H(Ri) received by final committee node members and H(Ri) is sent by the final committee node only
			txn_block - block of txns that the committee will agree on(intra committee consensus block)
			set_of_Rs - set of Ris obtained from the final committee of previous epoch
			newset_of_Rs - In the present epoch, set of Ris obtained from the final committee
			CommitteeConsensusData - a dictionary of committee ids that contains a dictionary of the txn block and the signatures
			finalBlockbyFinalCommittee - a dictionary of txn block and the signatures by the final committee members
			state - state in which a node is running
			mergedBlock - list of txns of different committees after their intra committee consensus
			finalBlock - agreed list of txns after pbft run by final committee
			RcommitmentSet - set of H(Ri)s received from the final committee after the consistency protocol [previous epoch values]
			newRcommitmentSet - For the present it contains the set of H(Ri)s received from the final committee after the consistency protocol
			finalCommitteeMembers - members of the final committee received from the directory committee
			txn- transactions stored by the directory members
			response - final block to be received by the client
			flag- to denote a bad or good node
			views - stores the ports of processes from which committee member views have been received
			primary- boolean to denote the primary node in the committee for PBFT run
			viewId - view number of the pbft
			pre_prepareMsgLog - log of pre-prepare msgs received during PBFT
			prepareMsgLog - log of prepare msgs received during PBFT
			commitMsgLog - log of commit msgs received during PBFT
			preparedData - data after prepared state
			committedData - data after committed state
			Finalpre_prepareMsgLog - log of pre-prepare msgs received during PBFT run by final committee
			FinalprepareMsgLog - log of prepare msgs received during PBFT run by final committee
			FinalcommitMsgLog - log of commit msgs received during PBFT run by final committee
			FinalpreparedData - data after prepared state in final pbft run
			FinalcommittedData - data after committed state in final pbft run
			faulty - Flag denotes whether this node is faulty or not
	"""

	def __init__(self):
		self.connection = pika.BlockingConnection(pika.ConnectionParameters(host='localhost'))
		self.IP = self.get_IP()
		self.port = self.get_port()
		self.key = self.get_key()
		self.PoW = {"hash" : "", "set_of_Rs" : "", "nonce" : 0}
		self.cur_directory = []
		self.identity = ""
		self.committee_id = ""
		# only when this node is the member of directory committee
		self.committee_list = dict()
		# only when this node is not the member of directory committee
		self.committee_Members = set()
		self.is_directory = False
		self.is_final = False
		self.epoch_randomness = self.initER()
		self.Ri = ""
		# only when this node is the member of final committee
		self.commitments = set()
		self.txn_block = []
		self.set_of_Rs = set()
		self.newset_of_Rs = set()
		self.CommitteeConsensusData = dict()
		self.CommitteeConsensusDataTxns = dict()
		self.finalBlockbyFinalCommittee = dict()
		self.finalBlockbyFinalCommitteeTxns = dict()
		self.state = ELASTICO_STATES["NONE"]
		self.mergedBlock = []
		self.finalBlock = {"sent" : False, "finalBlock" : [] }
		self.RcommitmentSet = ""
		self.newRcommitmentSet = ""
		self.finalCommitteeMembers = set()
		# only when this is the member of the directory committee
		self.txn = dict()
		self.response = []
		self.flag = True
		self.views = set()
		self.primary = False
		self.viewId = 0
		self.pre_prepareMsgLog = dict()
		self.prepareMsgLog = dict()
		self.commitMsgLog = dict()
		self.preparedData = dict()
		self.committedData = dict()

		# only when this is the part of final committee
		self.Finalpre_prepareMsgLog = dict()
		self.FinalprepareMsgLog = dict()
		self.FinalcommitMsgLog = dict()
		self.FinalpreparedData = dict()
		self.FinalcommittedData = dict()

		self.faulty = False


	def reset(self):
		"""
			reset some of the elastico class members
		"""
		try:
			self.IP = self.get_IP()
			self.key = self.get_key()

			channel = self.connection.channel()
			# to delete the queue in rabbitmq for next epoch
			channel.queue_delete(queue='hello' + str(self.port))
			channel.close()

			# self.port = self.get_port()
			self.PoW = {"hash" : "", "set_of_Rs" : "", "nonce" : 0}
			self.cur_directory = []
			self.identity = ""
			self.committee_id = ""
			# only when this node is the member of directory committee
			self.committee_list = dict()
			# only when this node is not the member of directory committee
			self.committee_Members = set()
			self.is_directory = False
			self.is_final = False
			self.Ri = ""
			# only when this node is the member of final committee
			self.commitments = set()
			self.txn_block = []
			self.set_of_Rs = self.newset_of_Rs
			self.newset_of_Rs = set()
			self.CommitteeConsensusData = dict()
			self.CommitteeConsensusDataTxns = dict()
			self.finalBlockbyFinalCommittee = dict()
			self.finalBlockbyFinalCommitteeTxns = dict()
			self.state = ELASTICO_STATES["NONE"]
			self.mergedBlock = []
			self.finalBlock = {"sent" : False, "finalBlock" : [] }
			self.RcommitmentSet = self.newRcommitmentSet
			self.newRcommitmentSet = ""
			self.finalCommitteeMembers = set()
			# only when this is the member of the directory committee
			self.txn = dict()
			self.response = []
			self.flag = True
			self.views = set()
			self.primary = False
			self.pre_prepareMsgLog = dict()
			self.viewId = 0
			self.prepareMsgLog = dict()
			self.commitMsgLog = dict()
			self.preparedData = dict()
			self.committedData = dict()

			# only when this is the part of final committee
			self.Finalpre_prepareMsgLog = dict()
			self.FinalprepareMsgLog = dict()
			self.FinalcommitMsgLog = dict()
			self.FinalpreparedData = dict()
			self.FinalcommittedData = dict()

			self.faulty = False

		except Exception as e:
			logging.error("error in reset", exc_info=e)
			raise e


	def initER(self):
		"""
			initialise r-bit epoch random string
		"""
		randomnum = random_gen(r)
		return ("{:0" + str(r) +  "b}").format(randomnum)


	def get_port(self):
		"""
			get port number for the process
		"""
		try:
			# acquire the lock
			lock.acquire()
			global port
			port.value += 1
		except Exception as e:
			logging.error("error in acquiring port lock" , exc_info=e)
			raise e
		finally:
			returnValue = port.value
			# release the lock
			lock.release()
			return returnValue


	def get_IP(self):
		"""
			for each node(processor) , get IP addr
			will return IP
		"""
		# ips = check_output(['hostname', '--all-ip-addresses'])
		# ips = ips.decode()
		# return ips.split(' ')[0]
		ip=""
		for i in range(4):
			ip += str(random_gen(8))
			ip += "."
		ip = ip[ : -1]
		return ip


	def get_key(self):
		"""
			for each node, it will return public pvt key pair
		"""
		key = RSA.generate(2048)
		return key


	def compute_PoW(self):
		"""
			returns hash which satisfies the difficulty challenge(D) : PoW["hash"]
		"""
		if self.state == ELASTICO_STATES["NONE"]:
			PK = self.key.publickey().exportKey().decode()
			IP = self.IP
			# If it is the first epoch , randomset_R will be an empty set .
			# otherwise randomset_R will be any c/2 + 1 random strings Ri that node receives from the previous epoch
			randomset_R = set()
			if len(self.set_of_Rs) > 0:
				self.epoch_randomness, randomset_R = self.xor_R()
			# compute the digest 
			digest = SHA256.new()
			digest.update(IP.encode())
			digest.update(PK.encode())
			digest.update(self.epoch_randomness.encode())
			digest.update(str(self.PoW["nonce"]).encode())
			hash_val = digest.hexdigest()
			if hash_val.startswith('0' * D):
				# hash starts with leading D 0's
				nonce = self.PoW["nonce"]
				self.PoW = {"hash" : hash_val, "set_of_Rs" : randomset_R, "nonce" : nonce}
				# change the state after solving the puzzle
				self.state = ELASTICO_STATES["PoW Computed"]
				return hash_val
			# try for other nonce 
			self.PoW["nonce"] += 1


	def notify_finalCommittee(self):
		"""
			notify the members of the final committee that they are the final committee members
		"""

		finalCommList = self.committee_list[fin_num]
		for finalMember in finalCommList:
			data = {"identity" : self.identity}
			# construct the msg
			msg = {"data" : data , "type" : "notify final member"}
			finalMember.send(msg)

	def get_committeeid(self, PoW):
		"""
			returns last s-bit of PoW["hash"] as Identity : committee_id
		""" 
		bindigest = ''
		for hashdig in PoW:
			bindigest += "{:04b}".format(int(hashdig, 16))
		identity = bindigest[-s:]
		return int(identity, 2)


	def form_identity(self):
		"""
			identity formation for a node
			identity consists of public key, ip, committee id, PoW, nonce, epoch randomness
		"""
		if self.state == ELASTICO_STATES["PoW Computed"]:
			
			# export public key
			PK = self.key.publickey().exportKey().decode()
			
			# set the committee id acc to PoW solution
			self.committee_id = self.get_committeeid(self.PoW["hash"])
			
			self.identity = Identity(self.IP, PK, self.committee_id, self.PoW, self.epoch_randomness,self.port)
			# changed the state after identity formation
			self.state = ELASTICO_STATES["Formed Identity"]
			return self.identity


	def is_OwnIdentity(self, identityobj):
		"""
			Checking whether the identityobj is the Elastico node's identity or not
		"""
		if self.identity == "":
			self.form_identity()
		return self.identity.isEqual(identityobj)


	def form_committee(self):
		"""
			creates directory committee if not yet created otherwise informs all
			the directory members
		""" 
		if len(self.cur_directory) < c:
			self.is_directory = True
			# ToDo: do all broadcast asynchronously
			# broadcast the identity to whole ntw
			BroadcastTo_Network(self.identity, "directoryMember")
			# change the state as it is the directory member
			self.state = ELASTICO_STATES["RunAsDirectory"]
		else:
			self.Send_to_Directory()
			if self.state != ELASTICO_STATES["Receiving Committee Members"]: 
				self.state = ELASTICO_STATES["Formed Committee"]

	def Send_to_Directory(self):
		"""
			Send about new nodes to directory committee members
		"""
		# Add the new processor in particular committee list of directory committee nodes
		for nodeId in self.cur_directory:
			msg = {"data" : self.identity, "type" : "newNode"}
			nodeId.send(msg)


	def checkCommitteeFull(self):
		"""
			directory member checks whether the committees are full or not
		"""
		commList = self.committee_list
		flag = 0
		# iterating over all committee ids
		for iden in range(pow(2,s)):
			if iden not in commList or len(commList[iden]) < c:
				logging.warning("committees not full  - bad miss id : %s", str(iden))
				flag = 1
				break
		if flag == 0:
			logging.warning("committees full  - good")
			if self.state == ELASTICO_STATES["RunAsDirectory after-TxnReceived"]:
				# notify the final members
				self.notify_finalCommittee()
				# multicast the txns and committee members to the nodes
				MulticastCommittee(commList, self.identity, self.txn)
				# change the state after multicast
				self.state = ELASTICO_STATES["RunAsDirectory after-TxnMulticast"]


	def unionViews(self, nodeData, incomingData):
		"""
			nodeData and incomingData are the set of identities
			Adding those identities of incomingData to nodeData that are not present in nodeData
		"""
		for data in incomingData:
			flag = False
			for nodeId in nodeData:
				# data is present already in nodeData
				if nodeId.isEqual(data):
					flag = True
					break
			if flag == False:
				# Adding the new identity
				nodeData.add(data)
		return nodeData

	def unionTxns(self, actualTxns, receivedTxns):
		"""
			union the transactions
		"""
		for transaction in receivedTxns:
			flag = True
			for txn in actualTxns:
				if txn.isEqual(transaction):
					flag = False
					break
			if flag:
				actualTxns.append(transaction)
		return actualTxns

	def receive(self, msg):
		"""
			method to recieve messages for a node as per the type of a msg
		"""
		try:
			# new node is added in directory committee if not yet formed
			if msg["type"] == "directoryMember":
				identityobj = msg["data"]
				# verify the PoW of the sender
				if self.verify_PoW(identityobj):
					if len(self.cur_directory) < c:
						# check whether identityobj is already present or not
						flag = True
						for obj in self.cur_directory:
							if identityobj.isEqual(obj):
								flag = False
								break
						if flag:
							# append the object if not already present
							self.cur_directory.append(identityobj)
				else:
					logging.error("%s  PoW not valid of an incoming directory member " , str(identityobj) )

			# new node is added to the corresponding committee list if committee list has less than c members
			elif msg["type"] == "newNode" and self.is_directory:
				identityobj = msg["data"]
				# verify the PoW
				if self.verify_PoW(identityobj):
					if identityobj.committee_id not in self.committee_list:
						# Add the identity in committee
						self.committee_list[identityobj.committee_id] = [identityobj]

					elif len(self.committee_list[identityobj.committee_id]) < c:
						# Add the identity in committee
						flag = True
						for obj in self.committee_list[identityobj.committee_id]:
							if identityobj.isEqual(obj):
								flag = False
								break
						if flag:
							self.committee_list[identityobj.committee_id].append(identityobj)
							if len(self.committee_list[identityobj.committee_id]) == c:
								# check that if all committees are full
								self.checkCommitteeFull()
				else:
					logging.error("PoW not valid in adding new node")

			# union of committe members views
			elif msg["type"] == "committee members views" and self.verify_PoW(msg["data"]["identity"]) and self.is_directory == False and msg["data"]["identity"].port not in self.views:
				# logging.warning("committee member views taken by committee id - %s" , str(self.committee_id))
				self.views.add(msg["data"]["identity"].port)
				commMembers = msg["data"]["committee members"]
				finalMembers  = msg["data"]["final Committee members"]

				if "txns" in msg["data"]:
					# update the txn block
					# ToDo: txnblock should be ordered, not set
					self.txn_block= self.unionTxns(self.txn_block, msg["data"]["txns"])
					logging.warning("I am primary %s", str(self.port))
					self.primary =  True
				# ToDo: verify this union thing
				# union of committee members wrt directory member
				self.committee_Members = self.unionViews(self.committee_Members, commMembers)
				# union of final committee members wrt directory member
				self.finalCommitteeMembers = self.unionViews(self.finalCommitteeMembers , finalMembers)
				# received the members
				# ToDo : Check and ensure that states are not overwritten
				if self.state == ELASTICO_STATES["Formed Committee"] and len(self.views) >= c //2 + 1:
					self.state = ELASTICO_STATES["Receiving Committee Members"]
				else:
					logging.error("Wrong state : %s", str(self.state))

			# receiving H(Ri) by final committe members
			elif msg["type"] == "hash" and self.isFinalMember():
				data = msg["data"]
				identityobj = data["identity"]
				if self.verify_PoW(identityobj):
					self.commitments.add(data["Hash_Ri"])

			elif msg["type"] == "RandomStringBroadcast":
				data = msg["data"]
				identityobj = data["identity"]
				if self.verify_PoW(identityobj):
					Ri = data["Ri"]
					HashRi = self.hexdigest(Ri)

					if HashRi in self.newRcommitmentSet:
						self.newset_of_Rs.add(Ri)
						if len(self.newset_of_Rs) >= c//2 + 1:
							logging.warning("received r by %s--%s" , str(self.port) , str(self.committee_id))
							self.state = ELASTICO_STATES["ReceivedR"]

			elif msg["type"] == "finalTxnBlock":
				data = msg["data"]
				identityobj = data["identity"]
				# verify the PoW of the sender
				if self.verify_PoW(identityobj):
					sign = data["signature"]
					received_commitmentSetList = data["commitmentSet"]
					PK = identityobj.PK
					finalTxnBlock = data["finalTxnBlock"]
					finalTxnBlock_signature = data["finalTxnBlock_signature"]
					# verify the signatures
					if self.verify_sign(sign, received_commitmentSetList, PK) and self.verify_signTxnList(finalTxnBlock_signature, finalTxnBlock, PK):

						# list init for final txn block
						finaltxnBlockDigest = txnHexdigest(finalTxnBlock)
						if finaltxnBlockDigest not in self.finalBlockbyFinalCommittee:
							self.finalBlockbyFinalCommittee[finaltxnBlockDigest] = []
							self.finalBlockbyFinalCommitteeTxns[finaltxnBlockDigest] = finalTxnBlock
						
						# creating the object that contains the identity and signature of the final member
						identityAndSign = IdentityAndSign(finalTxnBlock_signature, identityobj)
						
						# check whether this combination of identity and sign already exists or not
						flag = True
						for idSignObj in  self.finalBlockbyFinalCommittee[finaltxnBlockDigest]:
							if idSignObj.isEqual(identityAndSign):
								# it exists
								flag = False
								break
						if flag:
							# appending the identity and sign of final member
							self.finalBlockbyFinalCommittee[finaltxnBlockDigest].append(identityAndSign)

						# block is signed by sufficient final members and when the final block has not been sent to the client yet
						if len(self.finalBlockbyFinalCommittee[finaltxnBlockDigest]) >= c//2 + 1 and self.state != ELASTICO_STATES["FinalBlockSentToClient"]:

							# for final members, their state is updated only when they have also sent the finalblock to ntw
							if self.isFinalMember():
								if self.finalBlock["sent"]:
									self.state = ELASTICO_STATES["FinalBlockReceived"]
							else:
								self.state = ELASTICO_STATES["FinalBlockReceived"]

						if self.newRcommitmentSet == "":
							self.newRcommitmentSet = set()
						# union of commitments
						self.newRcommitmentSet |= set(received_commitmentSetList)
					else:
						logging.error("Signature invalid in final block received")
				else:
					logging.error("PoW not valid when final member send the block")

			# final committee member receives the final set of txns along with the signature from the node
			elif msg["type"] == "intraCommitteeBlock" and self.isFinalMember():
				data = msg["data"]
				identityobj = data["identity"]

				logging.warning("%s received the intra committee block from commitee id - %s- %s", str(self.port) , str(identityobj.committee_id) , str(identityobj.port))    
				if self.verify_PoW(identityobj):
					# verify the signatures
					if self.verify_signTxnList( data["sign"], data["txnBlock"] , identityobj.PK):
						if identityobj.committee_id not in self.CommitteeConsensusData:
							self.CommitteeConsensusData[identityobj.committee_id] = dict()
							self.CommitteeConsensusDataTxns[identityobj.committee_id] = dict()
						TxnBlockDigest = txnHexdigest( data["txnBlock"] )
						if TxnBlockDigest not in self.CommitteeConsensusData[identityobj.committee_id]:
							self.CommitteeConsensusData[identityobj.committee_id][ TxnBlockDigest ] = set()
							# store the txns for this digest

							self.CommitteeConsensusDataTxns[identityobj.committee_id][ TxnBlockDigest ] = data["txnBlock"]

						# add signatures for the txn block 
						self.CommitteeConsensusData[identityobj.committee_id][ TxnBlockDigest ].add( data["sign"] )
						logging.warning("intra committee block received by state - %s -%s- %s- receiver port%s" , str(self.state) ,str( identityobj.committee_id) , str(identityobj.port) , str(self.port))   
					else:
						logging.error("signature invalid for intra committee block")        
				else:
					logging.error("pow invalid for intra committee block")
			# ToDo: add verify of pows if reqd in below ifs
			
			elif msg["type"] == "command to run pbft":
				if self.is_directory == False:
					self.runPBFT(self.txn_block, msg["data"]["instance"])

			elif msg["type"] == "command to run pbft by final committee":
				if self.isFinalMember():
					self.runPBFT(self.mergedBlock, msg["data"]["instance"])

			elif msg["type"] == "send txn set and sign to final committee":
				if self.is_directory == False:
					self.SendtoFinal()

			elif msg["type"] == "verify and merge intra consensus data":
				if self.isFinalMember():
					self.verifyAndMergeConsensusData()

			elif msg["type"] == "send commitments of Ris":
				if self.isFinalMember():
					self.sendCommitment()

			elif msg["type"] == "broadcast final set of txns to the ntw":
				if self.isFinalMember():
					self.BroadcastFinalTxn()

			elif msg["type"] == "notify final member":
				logging.warning("notifying final member %s" , str(self.port))
				if self.verify_PoW(msg["data"]["identity"]) and self.committee_id == fin_num:
					self.is_final = True

			elif msg["type"] == "Broadcast Ri":
				if self.isFinalMember():
					self.BroadcastR()

			# ToDo: Add verification of pow here.
			elif msg["type"] == "reset-all":
				# reset the elastico node
				self.reset()

			elif msg["type"] == "pre-prepare" or msg["type"] == "prepare"or msg["type"] == "commit":
				self.pbft_process_message(msg)

			elif msg["type"] == "Finalpre-prepare" or msg["type"] == "Finalprepare" or msg["type"] == "Finalcommit":
				self.Finalpbft_process_message(msg)

		except Exception as e:
			# log the raised exception
			logging.error('Error at receive step ', exc_info=e)
			if isinstance(e, ConnectionRefusedError):
				logging.info("ConnectionRefusedError at port : %s", "!")
			raise e

	def verify_signTxnList(self, signature, TxnList, publickey):
		"""
			verify whether signature is valid or not 
			if public key is not key object then create a key object
		"""
		# decode the signature before verifying
		signature = base64.b64decode(signature)
		if type(publickey) is str:
			publickey = publickey.encode()
		if type(publickey) is bytes:
			publickey = RSA.importKey(publickey)
		# create digest of data
		digest = SHA256.new()
		for txn in TxnList:
			txnDigest = txn.hexdigest()
			digest.update(txnDigest.encode())
		verifier = PKCS1_v1_5.new(publickey)
		return verifier.verify(digest,signature)

	def pbft_process_message(self, msg):
		"""
			Process the messages related to Pbft!
		"""
		if msg["type"] == "pre-prepare":
			self.process_pre_prepareMsg(msg)
		elif msg["type"] == "prepare":
			self.process_prepareMsg(msg)
		elif msg["type"] == "commit":
			self.process_commitMsg(msg)


	def Finalpbft_process_message(self, msg):
		"""
			Process the messages related to Pbft!
		"""
		if msg["type"] == "Finalpre-prepare":
			self.process_Finalpre_prepareMsg(msg)
		elif msg["type"] == "Finalprepare":
			self.process_FinalprepareMsg(msg)
		elif msg["type"] == "Finalcommit":
			self.process_FinalcommitMsg(msg)


	def process_commitMsg(self, msg):
		"""
		"""
		# verify the commit message
		verified = self.verify_commit(msg)
		if verified:
			# Log the commit msgs!
			self.log_commitMsg(msg)


	def process_FinalcommitMsg(self, msg):
		"""
		"""
		# verify the commit message
		verified = self.verify_commit(msg)
		if verified:
			# Log the commit msgs!
			self.log_FinalcommitMsg(msg)


	def process_prepareMsg(self, msg):
		"""
		"""
		# verify the prepare message
		verified = self.verify_prepare(msg)
		if verified:
			# Log the prepare msgs!
			self.log_prepareMsg(msg)


	def process_FinalprepareMsg(self, msg):
		"""
		"""
		# verify the prepare message
		verified = self.verify_Finalprepare(msg)
		if verified:
			# Log the prepare msgs!
			self.log_FinalprepareMsg(msg)


	def isPrepared(self):
		"""
			Check if the state is prepared or not
		"""
		# collect prepared data
		preparedData = dict()
		f = (c - 1)//3
		# check for received request messages
		for socket in self.pre_prepareMsgLog:
			# In current View Id
			if self.pre_prepareMsgLog[socket]["pre-prepareData"]["viewId"] == self.viewId:
				# request msg of pre-prepare request
				requestMsg = self.pre_prepareMsgLog[socket]["message"]
				# digest of the message
				digest = self.pre_prepareMsgLog[socket]["pre-prepareData"]["digest"]
				# get sequence number of this msg
				seqnum = self.pre_prepareMsgLog[socket]["pre-prepareData"]["seq"]
				# find Prepare msgs for this view and sequence number
				if self.viewId in self.prepareMsgLog and seqnum in self.prepareMsgLog[self.viewId]:
					# need to find matching prepare msgs from different replicas atleast c//2 + 1
					count = 0
					for replicaId in self.prepareMsgLog[self.viewId][seqnum]:
						for msg in self.prepareMsgLog[self.viewId][seqnum][replicaId]:
							if msg["digest"] == digest:
								count += 1
								break
					# condition for Prepared state
					if count >= 2*f:

						if self.viewId not in preparedData:
							preparedData[self.viewId] = dict()
						if seqnum not in preparedData[self.viewId]:
							preparedData[self.viewId][seqnum] = list()
						preparedData[self.viewId][seqnum].append(requestMsg)
		if len(preparedData) > 0:
			self.preparedData = preparedData
			return True
		return False

	def isFinalPrepared(self):
		"""
			Check if the state is prepared or not
		"""
		# collect prepared data
		preparedData = dict()
		f = (c - 1)//3
		# check for received request messages
		for socket in self.Finalpre_prepareMsgLog:
			# In current View Id
			if self.Finalpre_prepareMsgLog[socket]["pre-prepareData"]["viewId"] == self.viewId:
				# request msg of pre-prepare request
				requestMsg = self.Finalpre_prepareMsgLog[socket]["message"]
				# digest of the message
				digest = self.Finalpre_prepareMsgLog[socket]["pre-prepareData"]["digest"]
				# get sequence number of this msg
				seqnum = self.Finalpre_prepareMsgLog[socket]["pre-prepareData"]["seq"]
				# find Prepare msgs for this view and sequence number
				if self.viewId in self.FinalprepareMsgLog and seqnum in self.FinalprepareMsgLog[self.viewId]:
					# need to find matching prepare msgs from different replicas atleast c//2 + 1
					count = 0
					for replicaId in self.FinalprepareMsgLog[self.viewId][seqnum]:
						for msg in self.FinalprepareMsgLog[self.viewId][seqnum][replicaId]:
							if msg["digest"] == digest:
								count += 1
								break
					# condition for Prepared state
					if count >= 2*f:

						if self.viewId not in preparedData:
							preparedData[self.viewId] = dict()
						if seqnum not in preparedData[self.viewId]:
							preparedData[self.viewId][seqnum] = list()
						preparedData[self.viewId][seqnum].append(requestMsg)
		if len(preparedData) > 0:
			self.FinalpreparedData = preparedData
			return True
		return False

	def isCommitted(self):
		"""
			Check if the state is committed or not
		"""
		# collect committed data
		committedData = dict()
		f = (c - 1)//3
		# check for received request messages
		for socket in self.pre_prepareMsgLog:
			# In current View Id
			if self.pre_prepareMsgLog[socket]["pre-prepareData"]["viewId"] == self.viewId:
				# request msg of pre-prepare request
				requestMsg = self.pre_prepareMsgLog[socket]["message"]
				# digest of the message
				digest = self.pre_prepareMsgLog[socket]["pre-prepareData"]["digest"]
				# get sequence number of this msg
				seqnum = self.pre_prepareMsgLog[socket]["pre-prepareData"]["seq"]
				if self.viewId in self.preparedData and seqnum in self.preparedData[self.viewId]:
					for prepareMsg in self.preparedData[self.viewId][seqnum]:
						if txnHexdigest(prepareMsg) == digest:
							# pre-prepared matched and prepared is also true, check for commits
							if self.viewId in self.commitMsgLog:
								if seqnum in self.commitMsgLog[self.viewId]:
									count = 0
									logging.warning("CHECK FOR COUNT IN COMMITTED BY PORT %s" , str(self.port))
									for replicaId in self.commitMsgLog[self.viewId][seqnum]:
										for msg in self.commitMsgLog[self.viewId][seqnum][replicaId]:
											if msg["digest"] == digest:
												count += 1
												break
									# ToDo: condition check 
									if count >= 2*f + 1:
										if self.viewId not in committedData:
											committedData[self.viewId] = dict()
										if seqnum not in committedData[self.viewId]:
											committedData[self.viewId][seqnum] = list()
										committedData[self.viewId][seqnum].append(requestMsg)
								else:
									logging.error("no seqnum found in commit msg log")
							else:
								logging.error("no view id found in commit msg log")
						else:
							logging.error("wrong digest in is committed")
				else:
					logging.error("view and seqnum not found in isCommitted")
			else:
				logging.error("wrong view in is committed")
		if len(committedData) > 0:
			self.committedData = committedData
			return True
		return False

	def isFinalCommitted(self):
		"""
			Check if the state is committed or not
		"""
		# collect committed data
		committedData = dict()
		f = (c - 1)//3
		# check for received request messages
		for socket in self.Finalpre_prepareMsgLog:
			# In current View Id
			if self.Finalpre_prepareMsgLog[socket]["pre-prepareData"]["viewId"] == self.viewId:
				# request msg of pre-prepare request
				requestMsg = self.Finalpre_prepareMsgLog[socket]["message"]
				# digest of the message
				digest = self.Finalpre_prepareMsgLog[socket]["pre-prepareData"]["digest"]
				# get sequence number of this msg
				seqnum = self.Finalpre_prepareMsgLog[socket]["pre-prepareData"]["seq"]
				if self.viewId in self.FinalpreparedData and seqnum in self.FinalpreparedData[self.viewId]:
					for prepareMsg in self.FinalpreparedData[self.viewId][seqnum]:
						if txnHexdigest(prepareMsg) == digest:
							# pre-prepared matched and prepared is also true, check for commits
							if self.viewId in self.FinalcommitMsgLog:
								if seqnum in self.FinalcommitMsgLog[self.viewId]:
									count = 0
									logging.warning("CHECK FOR COUNT IN COMMITTED BY PORT %s" , str(self.port))
									for replicaId in self.FinalcommitMsgLog[self.viewId][seqnum]:
										for msg in self.FinalcommitMsgLog[self.viewId][seqnum][replicaId]:
											if msg["digest"] == digest:
												count += 1
												break
									# ToDo: condition check 
									if count >= 2*f + 1:
										if self.viewId not in committedData:
											committedData[self.viewId] = dict()
										if seqnum not in committedData[self.viewId]:
											committedData[self.viewId][seqnum] = list()
										committedData[self.viewId][seqnum].append(requestMsg)
								else:
									logging.error("no seqnum found in commit msg log")
							else:
								logging.error("no view id found in commit msg log")
						else:
							logging.error("wrong digest in is committed")
				else:
					logging.error("view and seqnum not found in isCommitted")
			else:
				logging.error("wrong view in is committed")
		if len(committedData) > 0:
			self.FinalcommittedData = committedData
			return True
		return False



	def process_pre_prepareMsg(self, msg):
		"""
			Process Pre-Prepare msg
		"""
		# verify the pre-prepare message
		verified = self.verify_pre_prepare(msg)
		if verified:
			# Log the pre-prepare msgs!
			self.logPre_prepareMsg(msg)
		else:
			logging.error("error in verification of process_pre_prepareMsg")


	def process_Finalpre_prepareMsg(self, msg):
		"""
			Process Final Pre-Prepare msg
		"""
		# verify the Final pre-prepare message
		verified = self.verify_Finalpre_prepare(msg)
		if verified:
			# Log the final pre-prepare msgs!
			self.logFinalPre_prepareMsg(msg)
			# self.state = ELASTICO_STATES["PBFT_PRE_PREPARE"]
		else:
			logging.error("error in verification of Final process_pre_prepareMsg")


	def verify_commit(self, msg):
		"""
			verify commit msgs
		"""
		# verify Pow
		if not self.verify_PoW(msg["identity"]):
			return False
		# verify signatures of the received msg
		if not self.verify_sign(msg["sign"] , msg["commitData"] , msg["identity"].PK):
			return False
		# check the view is same or not
		if msg["commitData"]["viewId"] != self.viewId:
			return False
		return True


	def verify_prepare(self, msg):
		"""
			Verify prepare msgs
		"""
		# verify Pow
		if not self.verify_PoW(msg["identity"]):
			logging.warning("wrong pow in verify prepares")
			return False

		# verify signatures of the received msg
		if not self.verify_sign(msg["sign"] , msg["prepareData"] , msg["identity"].PK):
			logging.warning("wrong sign in verify prepares")
			return False

		# check the view is same or not
		if msg["prepareData"]["viewId"] != self.viewId:
			logging.warning("wrong view in verify prepares")
			return False

		# verifying the digest of request msg
		for socketId in self.pre_prepareMsgLog:
			pre_prepareMsg = self.pre_prepareMsgLog[socketId]
			if pre_prepareMsg["pre-prepareData"]["viewId"] == msg["prepareData"]["viewId"] and pre_prepareMsg["pre-prepareData"]["seq"] == msg["prepareData"]["seq"] and pre_prepareMsg["pre-prepareData"]["digest"] == msg["prepareData"]["digest"]:
				return True
		return False


	def verify_Finalprepare(self, msg):
		"""
			Verify final prepare msgs
		"""
		# verify Pow
		if not self.verify_PoW(msg["identity"]):
			logging.warning("wrong pow in verify final prepares")
			return False
		# verify signatures of the received msg
		if not self.verify_sign(msg["sign"] , msg["prepareData"] , msg["identity"].PK):
			logging.warning("wrong sign in verify final prepares")
			return False

		# check the view is same or not
		if msg["prepareData"]["viewId"] != self.viewId:
			logging.warning("wrong view in verify final prepares")
			return False

		# verifying the digest of request msg
		for socketId in self.Finalpre_prepareMsgLog:
			pre_prepareMsg = self.Finalpre_prepareMsgLog[socketId]
			if pre_prepareMsg["pre-prepareData"]["viewId"] == msg["prepareData"]["viewId"] and pre_prepareMsg["pre-prepareData"]["seq"] == msg["prepareData"]["seq"] and pre_prepareMsg["pre-prepareData"]["digest"] == msg["prepareData"]["digest"]:
				return True
		return False

	def verify_pre_prepare(self, msg):
		"""
			Verify pre-prepare msgs
		"""
		# verify Pow
		if not self.verify_PoW(msg["identity"]):
			logging.warning("wrong pow in  verify pre-prepare")
			return False
		# verify signatures of the received msg
		if not self.verify_sign(msg["sign"] , msg["pre-prepareData"] , msg["identity"].PK):
			logging.warning("wrong sign in  verify pre-prepare")
			return False
		# verifying the digest of request msg
		if txnHexdigest(msg["message"]) != msg["pre-prepareData"]["digest"]:
			logging.warning("wrong digest in  verify pre-prepare")
			return False
		# check the view is same or not
		if msg["pre-prepareData"]["viewId"] != self.viewId:
			logging.warning("wrong view in  verify pre-prepare")
			return False
		# check if already accepted a pre-prepare msg for view v and sequence num n with different digest
		seqnum = msg["pre-prepareData"]["seq"]
		for socket in self.pre_prepareMsgLog:
			if self.pre_prepareMsgLog[socket]["pre-prepareData"]["viewId"] == self.viewId and self.pre_prepareMsgLog[socket]["pre-prepareData"]["seq"] == seqnum:
				if msg["pre-prepareData"]["digest"] != self.pre_prepareMsgLog[socket]["pre-prepareData"]["digest"]:
					return False
		# If msg is discarded then what to do
		return True

	def verify_Finalpre_prepare(self, msg):
		"""
			Verify final pre-prepare msgs
		"""
		# verify Pow
		if not self.verify_PoW(msg["identity"]):
			logging.warning("wrong pow in  verify final pre-prepare")
			return False
		# verify signatures of the received msg
		if not self.verify_sign(msg["sign"] , msg["pre-prepareData"] , msg["identity"].PK):
			logging.warning("wrong sign in  verify final pre-prepare")
			return False
		# verifying the digest of request msg
		if txnHexdigest(msg["message"]) != msg["pre-prepareData"]["digest"]:
			logging.warning("wrong digest in  verify final pre-prepare")
			return False
		# check the view is same or not
		if msg["pre-prepareData"]["viewId"] != self.viewId:
			logging.warning("wrong view in  verify final pre-prepare")
			return False
		# check if already accepted a pre-prepare msg for view v and sequence num n with different digest
		seqnum = msg["pre-prepareData"]["seq"]
		for socket in self.Finalpre_prepareMsgLog:
			if self.Finalpre_prepareMsgLog[socket]["pre-prepareData"]["viewId"] == self.viewId and self.Finalpre_prepareMsgLog[socket]["pre-prepareData"]["seq"] == seqnum:
				if msg["pre-prepareData"]["digest"] != self.Finalpre_prepareMsgLog[socket]["pre-prepareData"]["digest"]:
					return False
		return True


	def log_prepareMsg(self, msg):
		"""
			log the prepare msg
		"""
		viewId = msg["prepareData"]["viewId"]
		seqnum = msg["prepareData"]["seq"]
		socketId = msg["identity"].IP +  ":" + str(msg["identity"].port)
		# add msgs for this view
		if viewId not in self.prepareMsgLog:
			self.prepareMsgLog[viewId] = dict()

		# add msgs for this sequence num
		if seqnum not in self.prepareMsgLog[viewId]:
			self.prepareMsgLog[viewId][seqnum] = dict()

		# add all msgs from this sender
		if socketId not in self.prepareMsgLog[viewId][seqnum]:
			self.prepareMsgLog[viewId][seqnum][socketId] = list()

		# ToDo: check that the msg appended is dupicate or not
		# log only required details from the prepare msg
		msgDetails = {"digest" : msg["prepareData"]["digest"], "identity" : msg["identity"]}
		# append msg to prepare msg log
		self.prepareMsgLog[viewId][seqnum][socketId].append(msgDetails)


	def log_FinalprepareMsg(self, msg):
		"""
			log the prepare msg
		"""
		viewId = msg["prepareData"]["viewId"]
		seqnum = msg["prepareData"]["seq"]
		socketId = msg["identity"].IP +  ":" + str(msg["identity"].port)
		# add msgs for this view
		if viewId not in self.FinalprepareMsgLog:
			self.FinalprepareMsgLog[viewId] = dict()

		# add msgs for this sequence num
		if seqnum not in self.FinalprepareMsgLog[viewId]:
			self.FinalprepareMsgLog[viewId][seqnum] = dict()

		# add all msgs from this sender
		if socketId not in self.FinalprepareMsgLog[viewId][seqnum]:
			self.FinalprepareMsgLog[viewId][seqnum][socketId] = list()

		# ToDo: check that the msg appended is dupicate or not
		# log only required details from the prepare msg
		msgDetails = {"digest" : msg["prepareData"]["digest"], "identity" : msg["identity"]}
		# append msg to prepare msg log
		self.FinalprepareMsgLog[viewId][seqnum][socketId].append(msgDetails)

	def log_commitMsg(self, msg):
		"""
			log the commit msg
		"""
		try:
			viewId = msg["commitData"]["viewId"]
			seqnum = msg["commitData"]["seq"]
			socketId = msg["identity"].IP +  ":" + str(msg["identity"].port)
			# add msgs for this view
			if viewId not in self.commitMsgLog:
				self.commitMsgLog[viewId] = dict()

			# add msgs for this sequence num
			if seqnum not in self.commitMsgLog[viewId]:
				self.commitMsgLog[viewId][seqnum] = dict()

			# add all msgs from this sender
			if socketId not in self.commitMsgLog[viewId][seqnum]:
				self.commitMsgLog[viewId][seqnum][socketId] = list()

			# log only required details from the commit msg
			msgDetails = {"digest" : msg["commitData"]["digest"], "identity" : msg["identity"]}
			# append msg
			logging.warning("Log committed msg for view %s, seqnum %s", str(viewId), str(seqnum))
			self.commitMsgLog[viewId][seqnum][socketId].append(msgDetails)		
		except Exception as e:
			raise e

	def log_FinalcommitMsg(self, msg):
		"""
			log the final commit msg
		"""
		try:
			viewId = msg["commitData"]["viewId"]
			seqnum = msg["commitData"]["seq"]
			socketId = msg["identity"].IP +  ":" + str(msg["identity"].port)
			# add msgs for this view
			if viewId not in self.FinalcommitMsgLog:
				self.FinalcommitMsgLog[viewId] = dict()

			# add msgs for this sequence num
			if seqnum not in self.FinalcommitMsgLog[viewId]:
				self.FinalcommitMsgLog[viewId][seqnum] = dict()

			# add all msgs from this sender
			if socketId not in self.FinalcommitMsgLog[viewId][seqnum]:
				self.FinalcommitMsgLog[viewId][seqnum][socketId] = list()

			# log only required details from the commit msg
			msgDetails = {"digest" : msg["commitData"]["digest"], "identity" : msg["identity"]}
			# append msg
			logging.warning("Log committed msg for view %s, seqnum %s", str(viewId), str(seqnum))
			self.FinalcommitMsgLog[viewId][seqnum][socketId].append(msgDetails)		
		except Exception as e:
			raise e

	def logPre_prepareMsg(self, msg):
		"""
			log the pre-prepare msg
		"""
		IP = msg["identity"].IP
		port = msg["identity"].port
		# create a socket
		socket = IP + ":" + str(port)
		self.pre_prepareMsgLog[socket] = msg

	def logFinalPre_prepareMsg(self, msg):
		"""
			log the pre-prepare msg
		"""
		IP = msg["identity"].IP
		port = msg["identity"].port
		# create a socket
		socket = IP + ":" + str(port)
		self.Finalpre_prepareMsgLog[socket] = msg

	def verifyAndMergeConsensusData(self):
		"""
			each final committee member validates that the values received from the committees are signed by 
			atleast c/2 + 1 members of the proper committee and takes the ordered set union of all the inputs
		"""
		logging.warning("verify and merge %s -- %s" , str(self.port) ,str(self.committee_id))
		for committeeid in range(pow(2,s)):
			if committeeid in self.CommitteeConsensusData:
				for txnBlockDigest in self.CommitteeConsensusData[committeeid]:
					if len(self.CommitteeConsensusData[committeeid][txnBlockDigest]) >= c//2 + 1:
						# get the txns from the digest
						txnBlock = self.CommitteeConsensusDataTxns[committeeid][txnBlockDigest]
						if len(txnBlock) > 0:
							# set_of_txns = ast.literal_eval(txnBlockDigest)
							# self.mergedBlock.extend(set_of_txns)
							self.mergedBlock = self.unionTxns(self.mergedBlock, txnBlock)
		if len(self.mergedBlock) > 0:
			self.state = ELASTICO_STATES["Merged Consensus Data"]
			logging.warning("%s - port , %s - mergedBlock" ,str(self.port) ,  str(self.mergedBlock))


	def runPBFT(self, instance):
		"""
			Runs a Pbft instance for the intra-committee consensus
		"""
		try:
			if self.state == ELASTICO_STATES["PBFT_NONE"]:
				if self.primary:
					# construct pre-prepare msg
					pre_preparemsg = self.construct_pre_prepare()
					# multicasts the pre-prepare msg to replicas
					# ToDo: what if primary does not send the pre-prepare to one of the nodes
					self.send_pre_prepare(pre_preparemsg)

					logging.warning("primary constructing pre-prepares with port %s" , str(self.port))
					
					# change the state of primary to pre-prepared 
					self.state = ELASTICO_STATES["PBFT_PRE_PREPARE_SENT"]
					# primary will log the pre-prepare msg for itself
					self.logPre_prepareMsg(pre_preparemsg)
				else:
					# for non-primary members
					if self.is_pre_prepared():
						self.state = ELASTICO_STATES["PBFT_PRE_PREPARE"]


			elif self.state == ELASTICO_STATES["PBFT_PRE_PREPARE"]:
				if not self.primary:
					# construct prepare msg
					# ToDo: verify whether the pre-prepare msg comes from various primaries or not
					preparemsgList = self.construct_prepare()
					logging.warning("constructing prepares with port %s" , str(self.port))
					self.send_prepare(preparemsgList)
					self.state = ELASTICO_STATES["PBFT_PREPARE_SENT"]

			# ToDo: primary has not changed its state to "PBFT_PREPARE_SENT"
			elif self.state ==ELASTICO_STATES["PBFT_PREPARE_SENT"] or self.state == ELASTICO_STATES["PBFT_PRE_PREPARE_SENT"]:
				logging.warning("prepared check by %s" , str(self.port))
				if self.isPrepared():
					logging.warning("prepared done by %s" , str(self.port))
					self.state = ELASTICO_STATES["PBFT_PREPARED"]

			elif self.state == ELASTICO_STATES["PBFT_PREPARED"]:
				commitMsgList = self.construct_commit()
				logging.warning("constructing commit with port %s" , str(self.port))
				self.send_commit(commitMsgList)
				self.state = ELASTICO_STATES["PBFT_COMMIT_SENT"]

			elif self.state == ELASTICO_STATES["PBFT_COMMIT_SENT"]:
				if self.isCommitted():
					logging.warning("committed done by %s" , str(self.port))
					self.state = ELASTICO_STATES["PBFT_COMMITTED"]
		except Exception as e:
			logging.error("error at run pbft", exc_info=e)
			raise e


	 runFinalPBFT(self , instance):
		"""
			Run PBFT by final committee members
		"""
		try:
			if self.state == ELASTICO_STATES["FinalPBFT_NONE"]:
				if self.primary:
					# construct pre-prepare msg
					finalpre_preparemsg = self.construct_Finalpre_prepare()
					# multicasts the pre-prepare msg to replicas
					self.send_pre_prepare(finalpre_preparemsg)

					logging.warning("final primary constructing pre-prepares with port %s" , str(self.port))
					
					# change the state of primary to pre-prepared 
					self.state = ELASTICO_STATES["FinalPBFT_PRE_PREPARE_SENT"]
					# primary will log the pre-prepare msg for itself
					self.logFinalPre_prepareMsg(finalpre_preparemsg)
				else:
					# for non-primary members
					if self.is_Finalpre_prepared():
						self.state = ELASTICO_STATES["FinalPBFT_PRE_PREPARE"]


			elif self.state == ELASTICO_STATES["FinalPBFT_PRE_PREPARE"]:
				if not self.primary:
					# construct prepare msg
					FinalpreparemsgList = self.construct_Finalprepare()
					logging.warning("constructing final prepares with port %s" , str(self.port))
					self.send_prepare(FinalpreparemsgList)
					self.state = ELASTICO_STATES["FinalPBFT_PREPARE_SENT"]

			# ToDo: primary has not changed its state to "FinalPBFT_PREPARE_SENT"
			elif self.state ==ELASTICO_STATES["FinalPBFT_PREPARE_SENT"] or self.state == ELASTICO_STATES["FinalPBFT_PRE_PREPARE_SENT"]:
				logging.warning("final prepared check by %s" , str(self.port))
				if self.isFinalPrepared():
					logging.warning("final prepared done by %s" , str(self.port))
					self.state = ELASTICO_STATES["FinalPBFT_PREPARED"]

			elif self.state == ELASTICO_STATES["FinalPBFT_PREPARED"]:
				commitMsgList = self.construct_Finalcommit()
				logging.warning("constructing final commit with port %s" , str(self.port))
				self.send_commit(commitMsgList)
				self.state = ELASTICO_STATES["FinalPBFT_COMMIT_SENT"]

			elif self.state == ELASTICO_STATES["FinalPBFT_COMMIT_SENT"]:
				if self.isFinalCommitted():
					logging.warning("final committed done by %s" , str(self.port))
					for viewId in self.FinalcommittedData:
						for seqnum in self.FinalcommittedData[viewId]:
							msgList = self.FinalcommittedData[viewId][seqnum]
							for msg in msgList:
								self.finalBlock["finalBlock"] = self.unionTxns(self.finalBlock["finalBlock"], msg)
					finalTxnBlock = self.finalBlock["finalBlock"]
					finalTxnBlock = list(finalTxnBlock)
					# order them! Reason : to avoid errors in signatures as sets are unordered
					# self.finalBlock["finalBlock"] = sorted(finalTxnBlock)
					logging.warning("final block by port %s with final block %s" , str(self.port), str(self.finalBlock["finalBlock"]))
					self.state = ELASTICO_STATES["FinalPBFT_COMMITTED"]
		except Exception as e:
			logging.error("error at run pbft", exc_info=e)
			raise e

	def is_pre_prepared(self):
		"""
			if the node received the pre-prepare msg from the primary
		"""
		return len(self.pre_prepareMsgLog) > 0

	def is_Finalpre_prepared(self):
		"""
		"""
		return len(self.Finalpre_prepareMsgLog) > 0	

	def send_prepare(self, prepareMsgList):
		"""
			send the prepare msgs to the committee members
		"""
		# send prepare msg list to committee members
		for preparemsg in prepareMsgList:
			for nodeId in self.committee_Members:
				nodeId.send(preparemsg)


	def construct_prepare(self):
		"""
			construct prepare msg in the prepare phase
		"""
		prepareMsgList = []
		# loop over all pre-prepare msgs
		for socketId in self.pre_prepareMsgLog:
			msg = self.pre_prepareMsgLog[socketId]
			# make prepare_contents Ordered Dict for signatures purpose
			prepare_contents =  OrderedDict({ "type" : "prepare" , "viewId" : self.viewId,  "seq" : msg["pre-prepareData"]["seq"] , "digest" : msg["pre-prepareData"]["digest"]})
		
			preparemsg = {"type" : "prepare",  "prepareData" : prepare_contents, "sign" : self.sign(prepare_contents) , "identity" : self.identity}
			prepareMsgList.append(preparemsg)
		return prepareMsgList

	def construct_Finalprepare(self):
		"""
			construct prepare msg in the prepare phase
		"""
		FinalprepareMsgList = []
		for socketId in self.Finalpre_prepareMsgLog:
			msg = self.Finalpre_prepareMsgLog[socketId]
			# make prepare_contents Ordered Dict for signatures purpose
			prepare_contents =  OrderedDict({ "type" : "Finalprepare" , "viewId" : self.viewId,  "seq" : msg["pre-prepareData"]["seq"] , "digest" : msg["pre-prepareData"]["digest"]})
		
			preparemsg = {"type" : "Finalprepare",  "prepareData" : prepare_contents, "sign" : self.sign(prepare_contents) , "identity" : self.identity}
			FinalprepareMsgList.append(preparemsg)
		return FinalprepareMsgList

	def construct_pre_prepare(self):
		"""
			construct pre-prepare msg , done by primary
		"""
		txnBlockList = self.txn_block
		# make pre_prepare_contents Ordered Dict for signatures purpose
		pre_prepare_contents =  OrderedDict({ "type" : "pre-prepare" , "viewId" : self.viewId, "seq" : 1 , "digest" : txnHexdigest(txnBlockList)})
		
		pre_preparemsg = {"type" : "pre-prepare", "message" : txnBlockList , "pre-prepareData" : pre_prepare_contents, "sign" : self.sign(pre_prepare_contents) , "identity" : self.identity}
		return pre_preparemsg 

	def construct_Finalpre_prepare(self):
		"""
			construct pre-prepare msg , done by primary final
		"""
		txnBlockList = self.mergedBlock
		# make pre_prepare_contents Ordered Dict for signatures purpose
		pre_prepare_contents =  OrderedDict({ "type" : "Finalpre-prepare" , "viewId" : self.viewId, "seq" : 1 , "digest" : txnHexdigest(txnBlockList)})
		
		pre_preparemsg = {"type" : "Finalpre-prepare", "message" : txnBlockList , "pre-prepareData" : pre_prepare_contents, "sign" : self.sign(pre_prepare_contents) , "identity" : self.identity}
		return pre_preparemsg 



	def send_commit(self, commitMsgList):
		"""
			send the commit msgs to the committee members
		"""
		for commitMsg in commitMsgList:
			for nodeId in self.committee_Members:
				nodeId.send(commitMsg)


	def construct_commit(self):
		"""
			Construct commit msgs
		"""
		commitMsges = []
		for viewId in self.preparedData:
			for seqnum in self.preparedData[viewId]:
				for msg in self.preparedData[viewId][seqnum]:
					digest = txnHexdigest(msg)
					# make commit_contents Ordered Dict for signatures purpose
					commit_contents = OrderedDict({"type" : "commit" , "viewId" : viewId , "seq" : seqnum , "digest":digest })
					commitMsg = {"type" : "commit" , "sign" : self.sign(commit_contents) , "commitData" : commit_contents, "identity" : self.identity}
					commitMsges.append(commitMsg)

		return commitMsges

	def construct_Finalcommit(self):
		"""
			Construct commit msgs
		"""
		commitMsges = []
		for viewId in self.FinalpreparedData:
			for seqnum in self.FinalpreparedData[viewId]:
				for msg in self.FinalpreparedData[viewId][seqnum]:
					digest = txnHexdigest(msg)
					# make commit_contents Ordered Dict for signatures purpose
					commit_contents = OrderedDict({"type" : "Finalcommit" , "viewId" : viewId , "seq" : seqnum , "digest":digest })
					commitMsg = {"type" : "Finalcommit" , "sign" : self.sign(commit_contents) , "commitData" : commit_contents, "identity" : self.identity}
					commitMsges.append(commitMsg)

		return commitMsges


	def send_pre_prepare(self, pre_preparemsg):
		"""
			Send pre-prepare msgs to all committee members
		"""
		# send pre-prepare msg to committee members
		for nodeId in self.committee_Members:
			# dont send pre-prepare msg to self
			if not self.identity.isEqual(nodeId):
				nodeId.send(pre_preparemsg)


	def isFinalMember(self):
		"""
			tell whether this node is a final committee member or not
		"""
		return self.is_final

	def sign(self,data):
		"""
			Sign the data i.e. signature
		"""
		# make sure that data is string or not
		if type(data) is not str:
			data = str(data)
		# create digest of data
		digest = SHA256.new()
		digest.update(data.encode())
		signer = PKCS1_v1_5.new(self.key)
		signature = signer.sign(digest)
		# encode the signature before sending
		signature = base64.b64encode(signature)
		return signature


	def verify_sign(self, signature, data, publickey):
		"""
			verify whether signature is valid or not 
			if public key is not key object then create a key object
		"""
		# decode the signature before verifying
		signature = base64.b64decode(signature)
		if type(publickey) is str:
			publickey = publickey.encode()
		if type(data) is not str:
			data = str(data)
		if type(publickey) is bytes:
			publickey = RSA.importKey(publickey)
		# create digest of data
		digest = SHA256.new()
		digest.update(data.encode())
		verifier = PKCS1_v1_5.new(publickey)
		return verifier.verify(digest,signature)


	def BroadcastFinalTxn(self):
		"""
			final committee members will broadcast S(commitmentSet), along with final set of 
			X(txn_block) to everyone in the network
		"""
		boolVal , S = consistencyProtocol()
		if boolVal == False:
			return S
		commitmentList = list(S)	
		PK = self.key.publickey().exportKey().decode()  
		data = {"commitmentSet" : commitmentList, "signature" : self.sign(commitmentList) , "identity" : self.identity , "finalTxnBlock" : self.finalBlock["finalBlock"] , "finalTxnBlock_signature" : self.signTxnList(self.finalBlock["finalBlock"])}
		logging.warning("finalblock- %s" , str(self.finalBlock["finalBlock"]))
		# final Block sent to ntw
		self.finalBlock["sent"] = True
		# A final node which is already in received state should not change its state
		if self.state != ELASTICO_STATES["FinalBlockReceived"]:
			logging.warning("change state to FinalBlockSent by %s" , str(self.port))
			self.state = ELASTICO_STATES["FinalBlockSent"]
		BroadcastTo_Network(data, "finalTxnBlock")

	def getCommittee_members(committee_id):
		"""
			Returns all members which have this committee id : committee_list[committee_id]
		"""
		pass


	def SendtoFinal(self):
		"""
			Each committee member sends the signed value(txn block after intra committee consensus)
			along with signatures to final committee
		"""

		PK = self.key.publickey().exportKey().decode()
		for viewId in self.committedData:
			for seqnum in self.committedData[viewId]:
				msgList = self.committedData[viewId][seqnum]
				for msg in msgList:
					self.txn_block= self.unionTxns(self.txn_block, msg)
		logging.warning("size of committee members %s" , str(len(self.finalCommitteeMembers)))
		logging.warning("send to final %s - %s--txns %s", str(self.committee_id) , str(self.port) , str(self.txn_block))
		for finalId in self.finalCommitteeMembers:
			# here txn_block is a set, since sets are unordered hence can't sign them. So convert set to list for signing
			txnBlock = self.txn_block
			data = {"txnBlock" : txnBlock , "sign" : self.signTxnList(txnBlock), "identity" : self.identity}
			msg = {"data" : data, "type" : "intraCommitteeBlock" }
			finalId.send(msg)
		self.state = ELASTICO_STATES["Intra Consensus Result Sent to Final"]


	def validate_signs(signatures):
		"""
			validate the signatures, should be atleast c/2 + 1 signs
		"""
		pass

	def signTxnList(self, TxnList):
		"""
		"""
		# create digest of data
		digest = SHA256.new()
		for txn in TxnList:
			txnDigest = txn.hexdigest()
			digest.update(txnDigest.encode())
		signer = PKCS1_v1_5.new(self.key)
		signature = signer.sign(digest)
		# encode the signature before sending
		signature = base64.b64encode(signature)
		return signature


	def generate_randomstrings(self):
		"""
			Generate r-bit random strings
		"""
		if self.isFinalMember() == True:
			Ri = random_gen(r)
			self.Ri = ("{:0" + str(r) +  "b}").format(Ri)


	def hexdigest(self, msg):
		"""
			returns the digest for a msg
		"""
		commitment = SHA256.new()
		if type(msg) is not str:
			msg = str(msg)
		commitment.update(msg.encode())
		return commitment.hexdigest()


	def getCommitment(self):
		"""
			generate commitment for random string Ri. This is done by a
			final committee member
		"""
		if self.isFinalMember() == True:
			if self.Ri == "":
				self.generate_randomstrings()
			commitment = SHA256.new()
			commitment.update(self.Ri.encode())
			return commitment.hexdigest()


	def sendCommitment(self):
		"""
			send the H(Ri) to the final committe members.This is done by a
			final committee member
		""" 
		if self.isFinalMember() == True:
			Hash_Ri = self.getCommitment()
			for nodeId in self.committee_Members:
				logging.warning("sent the commitment by %s" , str(self.port))
				data = {"identity" : self.identity , "Hash_Ri"  : Hash_Ri}
				msg = {"data" : data , "type" : "hash"}
				nodeId.send(msg)
			self.state = ELASTICO_STATES["CommitmentSentToFinal"]


	def addCommitment(self, finalBlock):
		"""
			ToDo: Check where to use this
			include H(Ri) ie. commitment in final block
		"""
		Hash_Ri = self.getCommitment()
		finalBlock["hash"] = Hash_Ri


	def BroadcastR(self):
		"""
			broadcast Ri to all the network, final member will do this
		"""
		if self.isFinalMember():
			data = {"Ri" : self.Ri, "identity" : self.identity}
			msg = {"data" : data , "type" : "RandomStringBroadcast"}
			self.state = ELASTICO_STATES["BroadcastedR"]
			BroadcastTo_Network(data, "RandomStringBroadcast")
		else:
			logging.error("non final member broadcasting R")    


	def xor_R(self):
		"""
			find xor of any random c/2 + 1 r-bit strings to set the epoch randomness
		"""
		randomset = SystemRandom().sample(self.set_of_Rs , c//2 + 1)
		xor_val = 0
		for R in randomset:
			xor_val = xor_val ^ int(R, 2)
		self.epoch_randomness = ("{:0" + str(r) +  "b}").format(xor_val)
		return ("{:0" + str(r) +  "b}").format(xor_val) , randomset


	def verify_PoW(self, identityobj):
		"""
			verify the PoW of the node identityobj
		"""
		PoW = identityobj.PoW

		# length of hash in hex
		if len(PoW["hash"]) != 64:
			return False

		# Valid Hash has D leading '0's (in hex)
		if not PoW["hash"].startswith('0' * D):
			return False

		# check Digest for set of Ri strings
		for Ri in PoW["set_of_Rs"]:
			digest = self.hexdigest(Ri)
			if digest not in self.RcommitmentSet:
				return False

		# reconstruct epoch randomness
		epoch_randomness = identityobj.epoch_randomness
		if len(PoW["set_of_Rs"]) > 0:
			xor_val = 0
			for R in PoW["set_of_Rs"]:
				xor_val = xor_val ^ int(R, 2)
			epoch_randomness = ("{:0" + str(r) +  "b}").format(xor_val)

		# recompute PoW 
		PK = identityobj.PK
		IP = identityobj.IP
		nonce = PoW["nonce"]

		digest = SHA256.new()
		digest.update(IP.encode())
		digest.update(PK.encode())
		digest.update(epoch_randomness.encode())
		digest.update(str(nonce).encode())
		hash_val = digest.hexdigest()
		if hash_val.startswith('0' * D) and hash_val == PoW["hash"]:
			# Found a valid Pow, If this doesn't match with PoW["hash"] then Doesnt verify!
			return True
		return False


	def appendToLedger(self):
		"""
			append the response to the ledger
		"""
		lock.acquire()
		if len(self.response) >= 1:
			finalCommittedBlock = self.response[0]
			# extracting transactions from the final committed block
			transactions = finalCommittedBlock.txnList

			# create a merkle tree of the transactions
			merkleTree = self.createMerkleTree(transactions)
			# get the transactions count
			txnCount = len(transactions)
			
			# For the genesis block
			prevBlockHash = ""
			signUpdated = False
			if len(ledger) > 0:
				LastBlock = ledger[-1]
				if LastBlock.getRootHash() == merkleTree.Get_Root_leaf():
					# Add signs here
					LastBlock.addSignAndIdentities(finalCommittedBlock.listSignaturesAndIdentityobjs)
					# Add commit msgs
					if is_directory == false and len(self.committee_Members) >= c:
						LastBlock.addintraCommitMsg(self.committedData)
						if self.isFinalMember():
							LastBlock.addfinalCommitMsg(self.FinalcommittedData)
					signUpdated = True
				else:
					prevBlockHash = LastBlock.hexdigest()
			if signUpdated == False:
				newBlock = Block(transactions, prevBlockHash, time.time(), len(ledger), txnCount, merkleTree)
				newBlock.addSignAndIdentities(finalCommittedBlock.listSignaturesAndIdentityobjs)
				ledger.append(newBlock)
		lock.release()

		if len(self.response) > 1:
			logging.error("Multiple Blocks coming!")


	def createMerkleTree(self, transactions):
		"""
			create a merkle tree and return the root hash
		"""
		merkleTree = MerkleTree(transactions)
		merkleTree.create_tree()
		return merkleTree


	def compute_fakePoW(self):
		"""
			bad node generates the fake PoW
		"""
		# random fakeness 
		index = random_gen(32)%3
		if index == 0:
			# Random hash with initial D hex digits 0s
			digest = SHA256.new()
			ranHash = digest.hexdigest()
			self.PoW["hash"] = D*'0' + ranHash[D:]

		elif index == 1:
			# computing an invalid PoW using less number of values in digest
			randomset_R = set()
			if len(self.set_of_Rs) > 0:
				self.epoch_randomness, randomset_R = self.xor_R()    
			while True:
				digest = SHA256.new()
				digest.update(str(self.PoW["nonce"]).encode())
				hash_val = digest.hexdigest()
				if hash_val.startswith('0' * D):
					nonce = self.PoW["nonce"]
					self.PoW = {"hash" : hash_val, "set_of_Rs" : randomset_R, "nonce" : nonce}
					break
				self.PoW["nonce"] += 1

		elif index == 2:
			# computing a random PoW
			randomset_R = set()
			if len(self.set_of_Rs) > 0:
				self.epoch_randomness, randomset_R = self.xor_R()    
			digest = SHA256.new()
			ranHash = digest.hexdigest()
			self.PoW = {"hash" : ranHash, "set_of_Rs" : randomset_R, "nonce" : random_gen()}

		logging.warning("computed fake POW %s" , str(index))
		self.state = ELASTICO_STATES["PoW Computed"]


	def consumeMsg(self):
		"""
			consume the msgs for this node
		"""
		try:
			# create a channel
			channel = self.connection.channel()
			# specify the queue name 
			queue = channel.queue_declare( queue='hello' + str(self.port))
			# count the number of messages that are in the queue
			count = queue.method.message_count

			# consume all the messages one by one
			while count:
				# get the message from the queue
				method_frame, header_frame, body = channel.basic_get('hello' + str(self.port))
				if method_frame:
					channel.basic_ack(method_frame.delivery_tag)
					data = pickle.loads(body)
					# consume the msg by taking the action in receive
					self.receive(data)
				count -= 1
			# close the channel 
			channel.close()
		except Exception as e:
			logging.warning("error in basic get %s",str(count),exc_info=e)


	def executePoW(self):
		"""
			execute PoW
		"""
		if self.flag:
			# compute Pow for good node
			self.compute_PoW()
		else:
			# compute Pow for bad node
			self.compute_fakePoW()


	def receiveTxns(self,epochTxn):
		"""
			directory node will receive transactions from client
		"""
		# Receive txns from client for an epoch
		k = 0
		num = len(epochTxn) // pow(2,s) 
		# loop in sorted order of committee ids
		for iden in range(pow(2,s)):
			if iden == pow(2,s)-1:
				# give all the remaining txns to the last committee
				self.txn[iden] = epochTxn[ k : ]
			else:
				self.txn[iden] = epochTxn[ k : k + num]
			k = k + num


	def checkCountForConsensusData(self):
		"""
			check the sufficient count for consensus data
		"""
		flag = False
		for commId in range(pow(2,s)):
			if commId not in self.CommitteeConsensusData:
				flag = True
				break
			else:
				for txnBlockDigest in self.CommitteeConsensusData[commId]:
					if len(self.CommitteeConsensusData[commId][txnBlockDigest]) <= c//2:
						flag = True
						logging.warning("bad committee id for intra committee block %s" , str(commId))
						break
		if flag == False:
			# when sufficient number of blocks from each committee are received
			logging.warning("good going for verify and merge")
			self.verifyAndMergeConsensusData()


	def checkCountForFinalData(self):
		"""
			check the sufficient counts for final data
		"""
		# collect final blocks sent by final committee and add the blocks to the response
		for txnBlockDigest in self.finalBlockbyFinalCommittee:
			if len(self.finalBlockbyFinalCommittee[txnBlockDigest]) >= c//2 + 1:
				TxnList = self.finalBlockbyFinalCommitteeTxns[txnBlockDigest]
				# create the final committed block that contatins the txnlist and set of signatures and identities to that txn list
				finalCommittedBlock = FinalCommittedBlock(TxnList, self.finalBlockbyFinalCommittee[txnBlockDigest])
				# add the block to the response
				self.response.append(finalCommittedBlock)
			else:
				logging.error("less block signs : %s", str(len(self.finalBlockbyFinalCommittee[txnBlockDigest])))

		if len(self.response) > 0:
			logging.warning("final block sent the block to client by %s", str(self.port))
			self.state = ELASTICO_STATES["FinalBlockSentToClient"]


	def execute(self, epochTxn):
		"""
			executing the functions based on the running state
		"""
		try:
			# print the current state of node for debug purpose
			print(self.identity ,  list(ELASTICO_STATES.keys())[ list(ELASTICO_STATES.values()).index(self.state)], "STATE of a committee member")

			# initial state of elastico node
			if self.state == ELASTICO_STATES["NONE"]:
				self.executePoW()				

			elif self.state == ELASTICO_STATES["PoW Computed"]:
				# form identity, when PoW computed
				self.form_identity()

			elif self.state == ELASTICO_STATES["Formed Identity"]:
				# form committee, when formed identity
				self.form_committee()

			elif self.is_directory and self.state == ELASTICO_STATES["RunAsDirectory"]:
				logging.warning("%s is the directory member" , str(self.port))
				self.receiveTxns(epochTxn)
				# directory member has received the txns for all committees 
				self.state  = ELASTICO_STATES["RunAsDirectory after-TxnReceived"]

			# when a node is part of some committee
			elif self.state == ELASTICO_STATES["Receiving Committee Members"]:
				if self.flag == False:
					# logging the bad nodes
					logging.error("member with invalid POW %s with commMembers : %s", self.identity , self.committee_Members)
				
				# Now The node should go for Intra committee consensus
				# initial state for the PBFT
				self.state = ELASTICO_STATES["PBFT_NONE"]
				# run PBFT for intra-committee consensus
				self.runPBFT("intra committee consensus")

			elif self.state == ELASTICO_STATES["PBFT_NONE"] or self.state == ELASTICO_STATES["PBFT_PRE_PREPARE"] or self.state ==ELASTICO_STATES["PBFT_PREPARE_SENT"] or self.state == ELASTICO_STATES["PBFT_PREPARED"] or self.state == ELASTICO_STATES["PBFT_COMMIT_SENT"] or self.state == ELASTICO_STATES["PBFT_PRE_PREPARE_SENT"]:
				# run pbft for intra consensus
				self.runPBFT("intra committee consensus")

			elif self.state == ELASTICO_STATES["PBFT_COMMITTED"]:
				# send pbft consensus blocks to final committee members
				logging.warning("pbft finished by members %s" , str(self.port))
				self.SendtoFinal()

			elif self.isFinalMember() and self.state == ELASTICO_STATES["Intra Consensus Result Sent to Final"]:
				# final committee node will collect blocks and merge them
				self.checkCountForConsensusData()

			elif self.isFinalMember() and self.state == ELASTICO_STATES["Merged Consensus Data"]:
				# final committee member runs final pbft
				self.state = ELASTICO_STATES["FinalPBFT_NONE"]
				self.runFinalPBFT("final committee consensus")

			elif self.state == ELASTICO_STATES["FinalPBFT_NONE"] or self.state == ELASTICO_STATES["FinalPBFT_PRE_PREPARE"] or self.state ==ELASTICO_STATES["FinalPBFT_PREPARE_SENT"] or self.state == ELASTICO_STATES["FinalPBFT_PREPARED"] or self.state == ELASTICO_STATES["FinalPBFT_COMMIT_SENT"] or self.state == ELASTICO_STATES["FinalPBFT_PRE_PREPARE_SENT"]:
				self.runFinalPBFT("final committee consensus")

			elif self.isFinalMember() and self.state == ELASTICO_STATES["FinalPBFT_COMMITTED"]:
				# send the commitment to other final committee members
				logging.warning("pbft finished by final committee %s" , str(self.port))
				self.sendCommitment()

			elif self.isFinalMember() and self.state == ELASTICO_STATES["CommitmentSentToFinal"]:
				# broadcast final txn block to ntw
				if len(self.commitments) >= c//2 + 1:
					self.BroadcastFinalTxn()

			elif self.state == ELASTICO_STATES["FinalBlockReceived"]:
				self.checkCountForFinalData()

			elif self.isFinalMember() and self.state == ELASTICO_STATES["FinalBlockSentToClient"]:
				# broadcast Ri is done when received commitment has atleast c/2  + 1 signatures
				if len(self.newRcommitmentSet) >= c//2 + 1:
					self.BroadcastR()

			elif self.state == ELASTICO_STATES["ReceivedR"]:
				self.appendToLedger()
				self.state = ELASTICO_STATES["LedgerUpdated"]

			elif self.state == ELASTICO_STATES["LedgerUpdated"]:
				# Now, the node can be reset
				return "reset"

		except Exception as e:
			# log the raised exception
			logging.error('Error at execute step ', exc_info=e)
			if isinstance(e, ConnectionRefusedError):
				logging.info("ConnectionRefusedError at port : %s", str(self.port))
			raise e

	def executeReset(self):
		"""
			call for reset
		"""
		logging.warning("call for reset for  %s" , str(self.port))
		if isinstance(self.identity, Identity):
		# if node has formed its identity
			msg = {"type": "reset-all", "data" : self.identity}
			self.identity.send(msg)
		else:
			# this node has not computed its identity,calling reset explicitly for node
			self.reset()


def executeSteps(nodeIndex, epochTxns , sharedObj):
	"""
		A process will execute based on its state and then it will consume
	"""
	global network_nodes

	node = network_nodes[nodeIndex]
	try:
		for epoch in epochTxns:
			# delete the entry of the node in sharedobj for the next epoch
			if nodeIndex in sharedObj:
				sharedObj.pop(nodeIndex)

			# epochTxn holds the txn for the current epoch
			epochTxn = epochTxns[epoch]
			startTime = time.time()
			while True:
				# execute one step of elastico node, execution of a node is done only when it has not done reset
				if nodeIndex not in sharedObj:
					response = node.execute(epochTxn)

					if response == "reset":
						# now reset the node
						node.executeReset()
						# adding the value reset for the node in the sharedobj
						sharedObj[nodeIndex] = "reset"

				# stop the faulty node in between
				if node.faulty == True and time.time() - startTime >= 60:
					logging.warning("bye bye!")
					break
				# All the elastico objects has done their reset
				if len(sharedObj) == n:
					break
				# process consume the msgs from the queue
				node.consumeMsg()
			# Ensuring that all nodes are reset and sharedobj is not affected
			time.sleep(60)

	except Exception as e:
		# log any error raised in the above try block
		logging.error('Error in  execute steps ', exc_info=e)
		raise e


def hexdigest(msg):
	"""
		returns the digest for a msg
	"""
	commitment = SHA256.new()
	if type(msg) is not str:
		msg = str(msg)
	commitment.update(msg.encode())
	return commitment.hexdigest()


def createProcesses(epochTxns, sharedObj):
	"""
		create the processes for each elastico node
	"""
	# list of processes
	processes = []
	for nodeIndex in range(n):
		# create a process
		process = Process(target= executeSteps, args=(nodeIndex, epochTxns, sharedObj))
		# add to the list of processes
		processes.append(process)
	return processes


def startAndJoinProcesses(processes):
	"""
		start and joins the processes
	"""
	for nodeIndex in range(n):
		print("process number" , nodeIndex , "started")
		# start the process
		processes[nodeIndex].start()

	for nodeIndex in range(n):
		# waits for the process to finish
		processes[nodeIndex].join()


def makeMalicious():
	"""
		make some nodes malicious who will compute wrong PoW
	"""
	global network_nodes
	# making some(4 here) nodes as malicious
	malicious_count = 0
	for i in range(malicious_count):
		badNodeIndex = random_gen(32)%n
		# set the flag false for bad nodes
		network_nodes[badNodeIndex].flag = False


def makeFaulty():
	"""
		make some nodes faulty who will stop participating in the protocol after sometime
	"""
	global network_nodes
	# making some(4 here) nodes as faulty
	faulty_count = 0
	for i in range(faulty_count):
		faultyNodeIndex = random_gen(32)%n
		# set the flag false for bad nodes
		network_nodes[faultyNodeIndex].faulty = True


def createNodes():
	"""
		create the elastico nodes
	"""
	# network_nodes is the list of elastico objects
	global network_nodes
	if len(network_nodes) == 0:
		for i in range(n):
			print( "---Running for processor number---" , i + 1)
			# Add the elastico obj to the list 
			network_nodes.append(Elastico())


def manageSharedData(manager):
	"""
		make shared variables
	"""
	global ledger, port, lock
	# share global port among processes
	port = manager.Value('i', 49152)
	# shared lock among processes
	lock=manager.Lock()
	# ledger - ledger is the database that contains the set of blocks where each block comes after an epoch
	ledger = manager.list()
	# sharedObj is the dict which denotes whether the nodeId has done reset or not in an epoch 
	sharedObj = manager.dict()
	return sharedObj



def Run(epochTxns):
	"""
		runs all the epochs
	"""
	global network_nodes, ledger

	try:
		# Manager for managing the shared variable among the processes
		manager = Manager()
		sharedObj = manageSharedData(manager)
		
		# create the elastico nodes
		createNodes()

		# make some nodes malicious and faulty
		makeMalicious()
		makeFaulty()
		
		# create the processes
		processes = createProcesses(epochTxns, sharedObj)
		# start and join the processes
		startAndJoinProcesses(processes)
		
		logging.warning("LEDGER-  %s, length - %s",str(ledger), str(len(ledger)))
		for block in ledger:
			logging.warning("txns : %s", str(block.data.transactions))
	except Exception as e:
		logging.error("error in run step" , exc_info=e)
		raise e


def createTxns():
	"""
		create txns for an epoch
	"""
	# txns is the list of the transactions in one epoch to which the committees will agree on
	txns = []
	# number of transactions in each epoch
	numOfTxns = 20
	for j in range(numOfTxns):
		# random amount
		random_num = random_gen()
		# craete the dummy transaction
		transaction = Transaction("a" , "b" , random_num, time.time())
		txns.append(transaction)
	return txns


if __name__ == "__main__":
	try:
		# logging module configured, will log in elastico.log file for each execution
		logging.basicConfig(filename='elastico.log',filemode='w',level=logging.WARNING)

		# epochTxns - dictionary that maps the epoch number to the list of transactions
		epochTxns = dict()
		numOfEpochs = 3
		for i in range(numOfEpochs):
			epochTxns[i] = createTxns()
		# run all the epochs 
		Run(epochTxns)

	except Exception as e:
		# log the exception raised
		logging.error('Error in  main ', exc_info=e)
		raise e

