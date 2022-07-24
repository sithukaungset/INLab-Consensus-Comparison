from math import floor

PBFT_TOTAL_NODES = 10
PBFT_F = floor((PBFT_TOTAL_NODES - 1) / 3)
PBFT_STATES = {"NONE": 0, "PRE-PREPARE": 1, "PREPARE": 2, "COMMIT": 3}


class PBFT:
    """
        Pbft class for implementing PBFT protocol, for consensus
        within a committee.
    """

    def __init__(self, blockChain):
        """
        """
        self.blockChain = blockChain
        self.node = blockChain.node
        self.pendingBlocks = {}
        self.prepareInfo = None
        self.commitInfo = {}
        self.state = PBFT_STATES["NONE"]
        pass

    def changeState(self):
        if self.state == PBFT_STATES["NONE"]:
            self.state = PBFT_STATES["PRE-PREPARE"]
        elif self.state == PBFT_STATES["PRE-PREPARE"]:
            self.state = PBFT_STATES["PREPARE"]
        elif self.state == PBFT_STATES["PREPARE"]:
            self.state = PBFT_STATES["COMMIT"]
        else:
            pass

    def has_block(self, block_hash):
        if block_hash in self.pendingBlocks:
            return True
        else:
            return False

    def is_busy(self):
        if self.state == PBFT_STATES["None"]:
            return False
        else:
            return True

    def add_block(self, block):
        # add block hash to pending blocks
        blockhash = block.get_hash()
        self.pendingBlocks[blockhash] = block

        if self.state == PBFT_STATES["None"]:
            # Do PRE-PREPARE
            pass

    def verify_request(self, msg):
        """
            Verify the request message m
            m := <c, d, t>
            where c - client id, d - data, t - timestamp
        """
        pass

    def Pre_Prepare(self, msg):
        """
            Send Pre-Prepare msgs
        """
        pass

    def verify_pre_prepare(self, msg):
        """
            Verify pre-prepare msgs
        """
        pass

    def Prepare(self, msg):
        """
            Send Prepare msgs
        """
        pass

    def verify_prepare(self, msg):
        """
        """
        pass

    def Commit(self, msg):
        """
            Send Commit msgs
        """
        pass

    def verify_commit(self, msg):
        """
        """
        pass

    def execute(self, msg):
        """
            Execute the request and send reply to client
            < v, t, c, i, r>
            where v = viewId, t = timestamp, c = client id, i = Peer(node) id, r = result of request
        """
        pass

    def process_message(self, msg):
        """
        """
        if self.state == PBFT_STATES["NONE"]:
            # verify the pre-prepare message
            verified = self.verify_pre_prepare(msg)
            if verified :
                self.changeState()
                # create prepare msg
                prepare_msg = ""
                # send prepare msgs to all
        elif self.state == PBFT_STATES["PRE-PREPARE"]:
            # verify the prepare message
            verified = self.verify_prepare(msg)
            if verified:
                # Increase prepare counts if this is not counted before
                # Msg should have signer's info
                if prepare_counts > PBFT_F:
                    self.changeState()
                    # create prepare msg
                    commit_msg = ""
                    # send commit msgs to all
        elif self.state == PBFT_STATES["PREPARE"]:
            # verify the commit message
            verified = self.verify_prepare(msg)
            if verified:
                # Increase commit counts if this is not counted before
                # Msg should have signer's info
                if commit_counts > PBFT_F:
                    self.changeState()
                    # Execute request's operation
                    execute()
                    # send reply msg to client
                    reply_msg = ""
