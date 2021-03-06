import copy
import os.path
import pathlib
from collections import defaultdict
from multiprocessing import Pool
import math
from os.path import exists
from sys import getsizeof
import random
import numpy as np
import matplotlib.pyplot as plt
from timer_cm import Timer
from scipy.stats import truncnorm
from statistics import mean
import logging


logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


class Simulator:

    OUTPUT_FOLDER = 'out'

    # Number of free cores on your CPU.
    PROCESSES = 20

    # Default nu,ber of transactions to run.
    TRANSACTIONS = 1000

    CLUSTERS = 16 * 3906  # *256
    NODES_PER_CLUSTER = 16
    NODES = CLUSTERS * NODES_PER_CLUSTER
    BUSINESSES_PERCENT = 0.05  # float % from 0 to 1

    TRANSACTIONS_P2P_PERCENT = 0.3  # float % from 0 to 1

    # Regulated by the total number of transaction runs at the moment.
    TRANSACTIONS_P2P_UNIQUE = 50  # Number of people that any person interacts with
    TRANSACTIONS_P2B_UNIQUE = 50  # Number of businesses that any person interacts with
    # TRANSACTIONS_PER_PERSON_AVG = 1000  # Per year
    # TRANSACTIONS_PER_PERSON_MIN = 500   # Per year
    # TRANSACTIONS_PER_PERSON_MAX = 5000  # Per year
    PAYROLL_FREQUENCY = 10   # Every Nth transaction payroll will be paid.
    PAYROLL_VOLUME = 0.8     # Percentage of cash spent on payroll each pay period. [0, 1]

    TRANSACTION_SIZE_MIN = 0.0001  # float % from net worth [0; 1]
    TRANSACTION_SIZE_MID = 0.01  # float % from net worth [0; 1]

    NET_WORTH_MIN = 10 * 100  # In cents
    NET_WORTH_AVG = 1000 * 100    # 1000 * 100  # In cents
    NET_WORTH_MAX = 1000 * 1000 * 100  # In cents

    # Bills per person
    BILLS_PP_MIN = 2
    BILLS_PP_AVG = 50
    BILLS_PP_MAX = 1000

    # X and Y field coordinate boundaries.
    MAX_FIELD_SIDE = 1000

    # What is considered to be a close distance between nodes.
    CLOSE_DISTANCE_RADIUS = MAX_FIELD_SIDE * 0.01  # 1% of a side.

    # Number of quadrants to which the field should be split. Counted in one axis split.
    AXIS_QUADRANTS = int(
        math.sqrt(
            float(NODES)/(TRANSACTIONS_P2P_UNIQUE+TRANSACTIONS_P2B_UNIQUE)
        )
    ) + 1   # +1 is an extra quadrant for the values at the edge.
    AXIS_QUADRANT_SIDE = float(MAX_FIELD_SIDE) / AXIS_QUADRANTS

    def __init__(self, debug=False):

        self.DEBUG = debug

        # Dict for current overall statistics.
        self.stats = {
            'nodes_total': None,
            'private_people_count': None,
            'businesses_count': None,
            'friends_per_person_mean': None,
            'businesses_per_person_mean': None,
            'money_in_system_total': None,  # In $, not cents.
            'bill_size_mean': None,
            'bills_count_total': None,
            'bills_per_person_avg': None,
            'wealth_max': None,
            'wealth_min': None,
            'wealth_mean': None,
            'bills_used_avg': None,
            'transaction_volume_avg': None,
            'wallets_size_mb': None,
            'bills_size_size_mb': None,
            'bills_cluster_size_mb': None,
            'b_receivers_size_mb': None,
            'p_receivers_size_mb': None,
            'generation': 0,
        }

        # {node id} -> (x, y)
        # Coordinates are saved in float.
        self.nodes_loc = {}

        # Quadrants are defined by a tuple key (x, y)
        # Defines which quadrant does the node belong to.
        # Deleted after payees generation.
        self.nodes_to_quadrant = {}
        # Defines which nodes belong to a quadrant.
        self.quadrants_to_nodes = defaultdict(list)

        # Next bill ID counter.
        self.next_bill_id = 0

        # List of available bill IDs to use.
        # [future_bill_id, future_bill_id2, ...]
        self.free_bill_ids = []

        # {bill_id} -> {size: 123, owner: node_id, cluster: cluster_id}
        self.bills = {}
        # {bill_id} -> 123.45
        self.bills_size = {}
        # {bill_id} -> 12
        self.bills_cluster = {}

        # {owner_id} -> [bill_id, bill_id, ...]
        self.wallets = {}

        # Set of all nodes that are businesses.
        # (business_id, business_id, business_id, ...)
        self.businesses = set()
        self.randomized_businesses = list()

        # Set of all nodes that are not businesses.
        # (private_node_id, private_node_id, private_node_id, ...)
        self.non_businesses = set()
        self.randomized_non_businesses = list()

        # Business receivers for each node to address.
        # {node_id} -> [business_id (node), business_id, business_id, ...]
        self.b_receivers = {}

        # Private receivers for each node to address.
        # {node_id} -> [person_id (node), person_id, person_id, ...]
        self.p_receivers = {}

        # Employees.
        # {business_id} -> [employee_id, employee_id, ...]
        self.employees = defaultdict(set)
        #
        # # Employee counters.
        # # {business_id} -> number_of_employees
        # self.employee_counters = defaultdict(lambda _: 0)   # Default is 0 employees

        # Make an output folder.
        current_path = pathlib.Path().resolve()
        run_number = 1
        while exists(os.path.join(current_path, self.OUTPUT_FOLDER, "run_{}".format(run_number))):
            run_number += 1
        self.output_path = os.path.join(current_path, self.OUTPUT_FOLDER, "run_{}".format(run_number))
        os.mkdir(self.output_path)

        with Timer('Generate nodes'):
            self.generate_nodes()

        with Timer('Generate payees'):
            # Non parallel version
            # payees = []
            # for i in range(NODES):
            #     payees.append(generate_node_payees(i))

            # nodes_buckets = np.array_split(range(self.NODES), self.PROCESSES)
            with Pool(self.PROCESSES) as pool:
                payees = pool.map(self.generate_node_payees, range(self.NODES))
                # payees[i][0] - business receivers for node i
                # payees[i][1] - private receivers for node i
                # payees[i][2] - employer id for node i

            # Free up memory from non-needed variables.
            del self.quadrants_to_nodes
            del self.nodes_to_quadrant

            for i in range(self.NODES):
                self.b_receivers[i] = payees[i][0]
                self.p_receivers[i] = payees[i][1]
                self.employees[payees[i][2]].add(i)

            # Add random employees to businesses with no employees.
            random_private_users = list(copy.deepcopy(self.non_businesses))
            random.shuffle(random_private_users)
            for i in self.businesses:
                if i not in self.employees.keys():
                    self.employees[i].add(random_private_users.pop())
            del random_private_users

        with Timer('Generate bills'):
            self.generate_bills()

    def run(self, transactions: int = 0):
        global log

        if not transactions:
            transactions = self.TRANSACTIONS

        self.system_status_bills_title()

        for i in range(transactions):
            with Timer("Transactions run #{}".format(i)):
                self.stats['generation'] = i

                # Decide if this is a payroll run or just regular transactions.
                if i and i % self.PAYROLL_FREQUENCY == 0:
                    payroll = True
                else:
                    payroll = False

                self.transactions_run(payroll)
                self.system_status_bills()

    @classmethod
    def run_transactions_thread(cls, args: tuple) -> (dict, dict):
        """
        Run transactions from the transactions_map using passed data.
        All the following params are passed as one tuple to simplify multiprocessing.
        :param transactions_map: List of tuples tu run transactions (from_node_id, to_node_id, amount(or None))
        :param wallets: Dict of wallets with node_id as a key.
        :param bills_size: Dict of bills sizes from self.bills_size with bill_id as a key.
        :param bills_cluster: Dict of bills clusters from self.bills_cluster with bill_id as a key.
        :param free_bill_ids: List of free bill ids to use.
        :return: (
            wallets,                # New wallets
            bills_size,
            bills_cluster,
            free_bill_ids,          # Free_bill_ids after the transaction
            bills_used_total,         # Total number of bills used in each transaction
            transaction_volume_total, # Total amount of each transaction
        )
        """

        global log
        transactions_map, wallets, bills_size, bills_cluster, free_bill_ids = args

        # Vars to keep track of stats.
        bills_used_total = 0
        transaction_volume_total = 0

        transaction_size_multipliers = [
            x/1e8 for x in cls.get_lognorm(
                cls.TRANSACTION_SIZE_MIN*1e8,
                cls.TRANSACTION_SIZE_MID*1e8,
                len(transactions_map)
            )
        ]

        i = 0
        for from_node_id, to_node_id, amount in transactions_map:
            # Get balance.
            if not amount:
                amount = int(cls.get_balance_static(from_node_id, wallets, bills_size) * transaction_size_multipliers.pop())
            transaction_volume_total += amount

            # If the transaction size is 0, we skipp it.
            if not amount:
                continue

            # Perform the transaction.
            bills_used_total += cls.send_amount(
                from_node_id,
                to_node_id,
                amount,
                wallets,
                bills_size,
                bills_cluster,
                free_bill_ids,
            )

            # Merge bills.
            free_bill_ids += cls.merge_nodes_bills(to_node_id, wallets, bills_size, bills_cluster)

            i += 1

        return wallets, bills_size, bills_cluster, free_bill_ids, bills_used_total, transaction_volume_total

    def transactions_run(self, payroll: bool = False):
        """Run a random set of transaction on the existing nodes in parallel."""

        # Prep service vars.
        node_to_bucket = {}
        # Transactions mappings (from node id, to node id)
        transactions_buckets = [[] for _ in range(self.PROCESSES)]
        # Node IDs that this bucket would interact with.
        nodes_buckets = [set() for _ in range(self.PROCESSES)]

        # Pick the nodes for the transaction.
        if payroll:
            # Only businesses distribute money.
            from_nodes = list(copy.deepcopy(self.businesses))
        else:
            # Everyone sends random transactions.
            from_nodes = [x for x in range(self.NODES)]
        random.shuffle(from_nodes)

        if payroll:
            for from_node_id in from_nodes:
                bucket = from_node_id % self.PROCESSES

                # Each business spend 80% of cash on salaries.
                amount = int(self.get_balance(from_node_id) * self.PAYROLL_VOLUME / len(self.employees[from_node_id]))

                for to_node_id in self.employees[from_node_id]:
                    transactions_buckets[bucket].append(
                        (
                            from_node_id,
                            to_node_id,
                            amount,
                        )
                    )

                    # Save the bucket mapping.
                    nodes_buckets[bucket].add(to_node_id)
                    ###
                    if to_node_id in node_to_bucket.keys():
                        if node_to_bucket[to_node_id] != bucket:
                            raise Exception
                    node_to_bucket[to_node_id] = bucket

                    nodes_buckets[bucket].add(from_node_id)
                    node_to_bucket[from_node_id] = bucket
        else:
            # Generate recipients.
            # from_node_id = random.randrange(self.NODES)
            to_nodes = [self.pick_recipient(x) for x in from_nodes]

            for i in range(self.NODES):
                from_node_id = from_nodes[i]
                from_node_bucket = node_to_bucket[from_node_id] if from_node_id in node_to_bucket.keys() else None
                to_node_id = to_nodes[i]
                to_node_bucket = node_to_bucket[to_node_id] if to_node_id in node_to_bucket.keys() else None

                if from_node_bucket is None and to_node_bucket is None:
                    from_node_bucket = i % self.PROCESSES
                    to_node_bucket = from_node_bucket
                elif from_node_bucket and to_node_bucket is None:
                    to_node_bucket = from_node_bucket
                elif from_node_bucket is None and to_node_bucket:
                    from_node_bucket = to_node_bucket
                elif from_node_bucket == to_node_bucket:
                    # All the buckets are already assigned properly.
                    pass
                else:
                    # Nodes are already in different buckets, the transaction is a no-go.
                    continue

                # Save the bucket mapping.
                node_to_bucket[from_node_id] = from_node_bucket
                node_to_bucket[to_node_id] = to_node_bucket

                # At this point from_node_bucket = to_node_bucket
                transactions_buckets[from_node_bucket].append(
                    (
                        from_node_id,
                        to_node_id,
                        None,           # Transaction amount is decided by the performing thread
                    )
                )

                # Save the IDs into the mapping.
                nodes_buckets[from_node_bucket].update([from_node_id, to_node_id])

        # Prepare free bill_ids for each bucket.
        # At most each thread will need as many IDs as # of transactions.
        free_bill_ids_buckets = []
        for i in range(self.PROCESSES):

            free_bill_ids_buckets.append([])
            ids_count = len(transactions_buckets[i])

            # First we use up free IDs.
            while self.free_bill_ids and ids_count > 0:
                free_bill_ids_buckets[i].append(self.free_bill_ids.pop())
                ids_count -= 1

            while ids_count > 0:
                free_bill_ids_buckets[i].append(self.next_bill_id)
                self.next_bill_id += 1
                ids_count -= 1

        free_bills_tmp = []
        for free_bill_ids_buckets_tmp in free_bill_ids_buckets:
            free_bills_tmp += free_bill_ids_buckets_tmp
        if len(set(free_bills_tmp)) != len(free_bills_tmp):
            raise Exception('Duplicate in free_bill_ids #b')

        # Split the data and run transactions.
        # List with process id as a pointer to the same structure as self.wallets
        wallets_split = [{} for _ in range(self.PROCESSES)]
        # List with process id as a pointer to the same structure as self.bills_size and self.bills_cluster
        bills_cluster_split = [{} for _ in range(self.PROCESSES)]
        bills_size_split = [{} for _ in range(self.PROCESSES)]

        for node_id, bucket_id in node_to_bucket.items():
            wallets_split[bucket_id][node_id] = self.wallets[node_id]
            for bill_id in wallets_split[bucket_id][node_id]:
                bills_size_split[bucket_id][bill_id] = self.bills_size[bill_id]
                bills_cluster_split[bucket_id][bill_id] = self.bills_cluster[bill_id]

        # Run transactions in parallel.
        # Arguments prep.
        parallel_args = []
        for i in range(self.PROCESSES):
            parallel_args.append(
                (
                    transactions_buckets[i],    # [0] - Map of transactions to perform.
                    wallets_split[i],           # [1] - Participating wallets.
                    bills_size_split[i],        # [2] - Participating bills' sizes.
                    bills_cluster_split[i],     # [3] - Participating bills' clusters.
                    free_bill_ids_buckets[i]    # [4] - List of free bill IDs for new bill creation.
                )
            )
        # Run transactions. Choose the method below and comment one out.
        # In parallel:
        with Pool(self.PROCESSES) as pool:
            parallel_res = pool.map(self.run_transactions_thread, parallel_args)
        # # In one thread sequentially:
        # parallel_res = []
        # for i in range(len(parallel_args)):
        #     parallel_res.append(
        #         self.run_transactions_thread(parallel_args[i])
        #     )

        # Merge the data back to the main pull.
        # Re-save the bills of the nodes that did not participate in this run.
        # parallel_res[bucket_id][0] <- New wallets
        # parallel_res[bucket_id][1] <- bills_size
        # parallel_res[bucket_id][2] <- bills_cluster
        # parallel_res[bucket_id][3] <- Free_bill_ids after the transaction
        # parallel_res[bucket_id][4] <- Average number of bills used in each transaction
        # parallel_res[bucket_id][5] <- Average amount of each transaction
        new_bills_size = {}
        new_bills_cluster = {}
        not_participating_nodes = set([x for x in range(self.NODES)]) - set(node_to_bucket.keys())
        for node_id in not_participating_nodes:
            for bill_id in self.wallets[node_id]:
                new_bills_size[bill_id] = self.bills_size[bill_id]
                new_bills_cluster[bill_id] = self.bills_cluster[bill_id]

        # Iterate over each node and collect data.
        for node_id, bucket_id in node_to_bucket.items():
            # Update the wallets.
            self.wallets[node_id] = parallel_res[bucket_id][0][node_id]     # [0] is wallets

            # Update bills from these wallets.
            for bill_id in self.wallets[node_id]:
                # Check if we are trying to overwrite an existing bill.
                if self.DEBUG and bill_id in new_bills_size.keys():
                    log.error("Trying to overwrite bill ID #{}".format(bill_id))
                new_bills_size[bill_id] = parallel_res[bucket_id][1][bill_id]    # [1] is bills_size
                new_bills_cluster[bill_id] = parallel_res[bucket_id][2][bill_id]    # [2] is bills_cluster

        # Vars for stats calculation.
        bills_used_total = 0
        transaction_volume_total = 0

        # Iterate over each process and update data.
        for bucket_id in range(self.PROCESSES):
            # Save free bill_ids back into the main bucket.
            self.free_bill_ids += parallel_res[bucket_id][3]    # [3] is free_bill_ids

            # Update stats.
            bills_used_total += parallel_res[bucket_id][4]    # [4] - Total number of bills used in all transactions.
            transaction_volume_total += parallel_res[bucket_id][5]    # [5] - Total amount of all transactions.

        # Finish up stats calculations.
        total_transactions = sum([len(parallel_args[x][0]) for x in range(self.PROCESSES)])
        self.stats['bills_used_avg'] = float(bills_used_total) / total_transactions
        self.stats['transaction_volume_avg'] = float(transaction_volume_total) / total_transactions

        if len(set(self.free_bill_ids)) != len(self.free_bill_ids):
            raise Exception('Duplicate in free_bill_ids after performing a full transaction run.')

        # Flush bills into the common pull.
        self.bills_size = new_bills_size
        self.bills_cluster = new_bills_cluster

    def pick_recipient(self, node_id):
        """Pick a recipient for a transaction from node_id."""
        if random.uniform(0, 1) > 0.8:
            # Transaction to a private person.
            return random.choice(list(self.p_receivers[node_id]))
        else:
            # Transaction to a business.
            return random.choice(list(self.b_receivers[node_id]))

    def update_system_status(self):
        """Updates self.stats with the latest data."""

        self.stats['nodes_total'] = len(self.nodes_loc)
        self.stats['friends_per_person_mean'] = mean([len(self.p_receivers[i]) for i in range(self.NODES)])
        self.stats['businesses_per_person_mean'] = mean([len(self.b_receivers[i]) for i in range(self.NODES)])

        self.stats['private_people_count'] = len(self.non_businesses)
        self.stats['businesses_count'] = len(self.businesses)
        self.stats['businesses_no_employees_count'] = len(self.businesses - set(self.employees.keys()))

        totals = [self.bills_size[x] for x in self.bills_size.keys()]
        self.stats['money_in_system_total'] = sum(totals)/100
        self.stats['bill_size_mean'] = mean(totals)/100
        self.stats['bills_count_total'] = len(self.bills_size.keys())
        self.stats['bills_per_person_avg'] = float(len(self.bills_size.keys()))/self.NODES

        wealth = [sum([self.bills_size[x] for x in self.wallets[i]]) for i in range(self.NODES)]
        self.stats['wealth_max'] = max(wealth)/100
        self.stats['wealth_min'] = min(wealth)/100
        self.stats['wealth_mean'] = mean(wealth)/100

        self.stats['wallets_size_mb'] = getsizeof(simulator.wallets) / 1000000
        self.stats['bills_size_size_mb'] = getsizeof(simulator.bills_size) / 1000000
        self.stats['bills_cluster_size_mb'] = getsizeof(simulator.bills_cluster) / 1000000
        self.stats['b_receivers_size_mb'] = getsizeof(simulator.b_receivers) / 1000000
        self.stats['p_receivers_size_mb'] = getsizeof(simulator.p_receivers) / 1000000

    def output_system_status(self, into_log=True, into_file=True):
        """
        Write out current system status.
        :param into_log: (True) Output into the log.
        :param into_file: (True) Output into the file.
        :return:
        """
        if not into_log and not into_file:
            return

        self.update_system_status()

        if into_file:
            with open(os.path.join(self.output_path, 'system_status.tsv'), "a") as fp:
                output = "---SYSTEM STATUS on Gen. {}---\n".format(self.stats['generation'])
                output += "Total nodes:\t{}\n".format(self.stats['nodes_total'])
                output += "Total private people count:\t{}\n".format(self.stats['private_people_count'])
                output += "Total businesses count:\t{}\n".format(self.stats['businesses_count'])
                output += "Businesses without employees count:\t{}\n".format(self.stats['businesses_no_employees_count'])
                output += "Total $ in the system:\t{}\n".format(self.stats['money_in_system_total'])
                output += "Total # of bills:\t{}\n".format(self.stats['bills_count_total'])
                output += "Mean bill size:\t{}\n".format(self.stats['bill_size_mean'])
                output += "Avg # of bills per person:\t{}\n".format(self.stats['bills_per_person_avg'])
                output += "Mean wealth per person:\t{}\n".format(self.stats['wealth_mean'])
                output += "Max wealth per person:\t{}\n".format(self.stats['wealth_max'])
                output += "Min wealth per person:\t{}\n".format(self.stats['wealth_min'])
                output += "Mean friends per person:\t{}\n".format(self.stats['friends_per_person_mean'])
                output += "Mean businesses per person:\t{}\n".format(self.stats['businesses_per_person_mean'])
                output += "Wallets storage size (MB):\t{}\n".format(self.stats['wallets_size_mb'])
                output += "Bills storage size (MB):\t{}\n".format(self.stats['bills_size_size_mb'])
                output += "Bills cluster storage size (MB):\t{}\n".format(self.stats['bills_cluster_size_mb'])
                output += "Business receivers storage size (MB):\t{}\n".format(self.stats['b_receivers_size_mb'])
                output += "Personal receivers storage size (MB):\t{}\n".format(self.stats['p_receivers_size_mb'])
                output += "\n"
                fp.write(output)

        if into_log:
            global log

            log.info("---------System Status on Gen. {}---------".format(self.stats['generation']))
            log.info("Total nodes: {}".format(self.stats['nodes_total']))
            log.info("Total private people count: {}".format(self.stats['private_people_count']))
            log.info("Total businesses count: {}".format(self.stats['businesses_count']))
            log.info("Businesses without employees count: {}".format(self.stats['businesses_no_employees_count']))
            log.info("Total $ in the system: {}".format(self.stats['money_in_system_total']))
            log.info("Total # of bills: {}".format(self.stats['bills_count_total']))
            log.info("Mean bill size: {}".format(self.stats['bill_size_mean']))
            log.info("Avg # of bills per person: {}".format(self.stats['bills_per_person_avg']))
            log.info("Mean wealth per person: {}".format(self.stats['wealth_mean']))
            log.info("Max wealth per person: {}".format(self.stats['wealth_max']))
            log.info("Min wealth per person: {}".format(self.stats['wealth_min']))
            log.info("Mean friends per person: {}".format(self.stats['friends_per_person_mean']))
            log.info("Mean businesses per person: {}".format(self.stats['businesses_per_person_mean']))
            log.info("Wallets storage size: {} MB".format(self.stats['wallets_size_mb']))
            log.info("Bills storage size: {} MB".format(self.stats['bills_size_size_mb']))
            log.info("Bills cluster storage size: {} MB".format(self.stats['bills_cluster_size_mb']))
            log.info("Business receivers storage size: {} MB".format(self.stats['b_receivers_size_mb']))
            log.info("Personal receivers storage size: {} MB".format(self.stats['p_receivers_size_mb']))

        """ Graphs """
        # Wealth graph.
        plt.hist([sum([self.bills_size[x] for x in self.wallets[i]]) / 100 for i in range(self.NODES)], bins=1000)
        plt.xlim([0, self.NET_WORTH_AVG / 100 * 4])
        plt.xlabel("$ per person")
        plt.ylabel("# of ppl with this wealth")

        if into_log:
            plt.show()
        if into_file:
            plt.savefig(
                os.path.join(self.output_path, "wealth_spread_overall_gen_{}.png".format(self.stats['generation'])),
                bbox_inches='tight',
            )
        plt.clf()

        # Businesses wealth graph.
        plt.hist([sum([self.bills_size[x] for x in self.wallets[i]]) / 100 for i in self.businesses], bins=1000)
        plt.xlim([0, self.NET_WORTH_AVG / 10])
        plt.xlabel("$ per business")
        plt.ylabel("# of businesses with this wealth")

        if into_log:
            plt.show()
        if into_file:
            plt.savefig(
                os.path.join(self.output_path, "wealth_spread_businesses_gen_{}.png".format(self.stats['generation'])),
                bbox_inches='tight',
            )
        plt.clf()

        # Individuals' wealth graph.
        plt.hist([sum([self.bills_size[x] for x in self.wallets[i]]) / 100 for i in self.non_businesses], bins=1000)
        plt.xlim([0, self.NET_WORTH_AVG / 10])
        plt.xlabel("$ per non-business person")
        plt.ylabel("# of non-businesses with this wealth")

        if into_log:
            plt.show()
        if into_file:
            plt.savefig(
                os.path.join(self.output_path, "wealth_spread_individuals_gen_{}.png".format(self.stats['generation'])),
                bbox_inches='tight',
            )
        plt.clf()

    def system_status_bills_title(self):
        with open(os.path.join(self.output_path, 'transactions_log.tsv'), "a") as fp:
            fp.write(
                "Transaction Generations"
                "\tTotal bills"
                "\tAvg bill count per person"
                "\tMean bill size"
                "\tAvg number of bills used per transaction"
                "\tAvg transaction volume"
                "\n"
            )

    def system_status_bills(self):
        self.update_system_status()

        with open(os.path.join(self.output_path, 'transactions_log.tsv'), "a") as fp:
            fp.write(
                "{}\t{}\t{}\t{}\t{}\t{}\n".format(
                    self.stats['generation'],
                    self.stats['bills_count_total'],
                    self.stats['bills_per_person_avg'],
                    self.stats['bill_size_mean'],
                    self.stats['bills_used_avg'],
                    self.stats['transaction_volume_avg'],
                )
            )

    def close_dist(self, distance):
        """Returns True if the distance is close."""
        return distance <= self.CLOSE_DISTANCE_RADIUS

    def far_dist(self, distance):
        """Returns True if the distance is far."""
        return not self.close_dist(distance)

    @classmethod
    def get_lognorm(cls, low, mid, n):
        assert low < mid
        mu = math.log(mid-low)/1.05
        sigma = math.sqrt((math.log(mid-low) - mu) * 2)
        return np.random.lognormal(mu, sigma, n)+low

    def get_asymmetric_norm(self, low, mid, upp):
        """Get one random number from an unbalanced normal distribution."""
        # if random.uniform(0, 1) < 0.5:
        if random.uniform(low, upp) < mid:
            # Less than a mean
            upp = mid
        else:
            # Greater than a mean
            low = mid

        sd = (upp - low) / 3
        distribution = truncnorm((low - mid) / sd, (upp - mid) / sd, loc=mid, scale=sd)
        return distribution.rvs().round().astype(int)

    @classmethod
    def node_to_cluster(cls, node_id: int) -> int:
        """Get cluster_id that node_id belongs to."""
        return node_id // cls.NODES_PER_CLUSTER

    def distance_between_nodes(self, node_id_from: int, node_id_to: int) -> float:
        """Get distance between two node IDs."""
        return math.sqrt(
            (self.nodes_loc[node_id_from][0] - self.nodes_loc[node_id_to][0])**2
            + (self.nodes_loc[node_id_from][1] - self.nodes_loc[node_id_to][1])**2
        )

    def generate_nodes(self):

        # Generate nodes.
        for i in range(self.NODES):
            # Empty wallets are created by default.

            # Set location.
            loc = (float(random.randrange(self.MAX_FIELD_SIDE)), float(random.randrange(self.MAX_FIELD_SIDE)))
            self.nodes_loc[i] = loc

            # Set quadrants.
            quadrant = (int(loc[0]/self.AXIS_QUADRANT_SIDE), int(loc[1]/self.AXIS_QUADRANT_SIDE))
            self.nodes_to_quadrant[i] = quadrant
            self.quadrants_to_nodes[quadrant].append(i)

            # Pick if the node is a business.
            if random.randrange(100) <= 100 * self.BUSINESSES_PERCENT:
                self.businesses.add(i)
            else:
                self.non_businesses.add(i)

        # Set defaults for randomized businesses and non businesses.
        self.randomized_businesses = list(self.businesses)
        random.shuffle(self.randomized_businesses)
        self.randomized_non_businesses = list(self.non_businesses)
        random.shuffle(self.randomized_non_businesses)

    def generate_node_payees(self, node_id):
        """
        Generate lists of close businesses and friends for the node_id node
        :param node_id: Which node to generate payees for
        :return: (list of business payees, list of private payees, employer ID)
        """

        # Get node's quadrant.
        node_quadrant_x, node_quadrant_y = self.nodes_to_quadrant[node_id]

        # Set self business flag.
        is_a_business = node_id in self.businesses

        # Create empty lists of receivers.
        self.p_receivers[node_id] = set()
        self.b_receivers[node_id] = set()

        # Var to store the employer ID
        employer_id = None

        # Pick number of friends and businesses.
        n_friends = self.get_asymmetric_norm(10, 60, 200)
        n_close_friends = n_friends / 2
        n_far_friends = n_friends - n_close_friends
        n_businesses = self.get_asymmetric_norm(10, 60, 200)
        n_close_businesses = n_businesses / 2
        n_far_businesses = n_businesses - n_close_businesses

        close_businesses = set()
        far_businesses = set()
        close_friends = set()
        far_friends = set()

        # Generate random far businesses.
        while len(far_businesses) < n_far_businesses:
            # Pick a random business ID.
            k = self.randomized_businesses[random.randrange(len(self.randomized_businesses))]

            # If the business is close, and we need one, we save it.
            if self.close_dist(self.distance_between_nodes(node_id, k)):
                if len(close_businesses) < n_close_businesses:
                    close_businesses.add(k)
            # If the business is far, we save it.
            # No need to check if we already have enough far businesses, it's checked by the main loop.
            else:
                far_businesses.add(k)

        # Generate random far friends.
        while len(far_friends) < n_far_friends:
            # Pick a random friend ID.
            k = self.randomized_non_businesses[random.randrange(len(self.randomized_non_businesses))]

            # If the business is close, and we need one, we save it.
            if self.close_dist(self.distance_between_nodes(node_id, k)):
                if len(close_friends) < n_close_friends:
                    close_friends.add(k)
            # If the friend is far, we save it.
            # No need to check if we already have enough far friends, it's checked by the main loop.
            else:
                far_friends.add(k)

        # Generate random close businesses and friends.
        if len(close_businesses) < n_close_businesses or len(close_friends) < n_close_friends:
            # Close businesses are picked out of neighboring quadrants only.
            # No need to worry about uniqueness, nodes only belong to one quadrant.
            close_nodes_candidates = []
            # Add nodes from neighboring quadrants.
            # Because quadrants_to_nodes is a default list, it just adds empty lists for non-existent quadrants.
            close_nodes_candidates += self.quadrants_to_nodes[(node_quadrant_x-1, node_quadrant_y-1)]
            close_nodes_candidates += self.quadrants_to_nodes[(node_quadrant_x-1, node_quadrant_y)]
            close_nodes_candidates += self.quadrants_to_nodes[(node_quadrant_x-1, node_quadrant_y+1)]
            close_nodes_candidates += self.quadrants_to_nodes[(node_quadrant_x, node_quadrant_y-1)]
            close_nodes_candidates += self.quadrants_to_nodes[(node_quadrant_x, node_quadrant_y)]
            close_nodes_candidates += self.quadrants_to_nodes[(node_quadrant_x, node_quadrant_y+1)]
            close_nodes_candidates += self.quadrants_to_nodes[(node_quadrant_x+1, node_quadrant_y-1)]
            close_nodes_candidates += self.quadrants_to_nodes[(node_quadrant_x+1, node_quadrant_y)]
            close_nodes_candidates += self.quadrants_to_nodes[(node_quadrant_x+1, node_quadrant_y+1)]
            # Shuffle the results.
            random.shuffle(close_nodes_candidates)

            # Walk through each node ID.
            for k in close_nodes_candidates:
                # Exit if we have enough close businesses and friends.
                if len(close_businesses) >= n_close_businesses and len(close_friends) >= n_close_friends:
                    break

                # If the node is far, we don't need it.
                if self.close_dist(self.distance_between_nodes(node_id, k)):
                    continue

                # Check if the node is a business or a consumer.
                if k in self.businesses:
                    # First and foremost we pick an employer.
                    # Except for when the node is a business itself.
                    if not is_a_business:
                        if not employer_id:
                            employer_id = k
                            continue

                    # If we still need close businesses.
                    if len(close_businesses) < n_close_businesses:
                        close_businesses.add(k)
                # If the node is a consumer, and we need one.
                elif len(close_friends) < n_close_friends:
                    close_friends.add(k)

            # Free up memory.
            del close_nodes_candidates

            # There are theoretically might not be enough close businesses or friends at this point,
            # but it means that they don't exist.

        return(
            # We mix close and far businesses up because the payments send rate is uniform.
            # If it's not, you need to separate this and keep track of them separately.
            close_businesses.union(far_businesses),
            # We mix close and far friends up because the payments send rate is uniform.
            # If it's not, you need to separate this and keep track of them separately.
            close_friends.union(far_friends),
            # Node ID of the employer for this node.
            employer_id,
        )

    def generate_node_payees_bulk(self, node_ids):
        res = []
        for i in node_ids:
            res.append(self.generate_node_payees(i))
        return res

    def generate_node_bills(self, bill_ids, total, node_id):
        bills_size_part = {}
        bills_cluster_part = {}

        # Split total into specific bills.
        node_bills = self.split_int(total, len(bill_ids))
        for i in range(len(bill_ids)):
            bill_size = float(node_bills[i])
            bill_id = bill_ids[i]
            bills_size_part[bill_id] = bill_size
            bills_cluster_part[bill_id] = random.randrange(self.CLUSTERS)

        return bills_size_part, bills_cluster_part

    def generate_bills(self):

        # with Pool(self.PROCESSES) as pool:
        #     totals = pool.starmap(self.get_asymmetric_norm, [(self.NET_WORTH_MIN, self.NET_WORTH_AVG, self.NET_WORTH_MAX) for _ in range(self.NODES)])
        #     n_bills = pool.starmap(self.get_asymmetric_norm, [(self.BILLS_PP_MIN, self.BILLS_PP_AVG, self.BILLS_PP_MAX) for _ in range(self.NODES)])

        totals = np.int_(self.get_lognorm(self.NET_WORTH_MIN, self.NET_WORTH_AVG, self.NODES))
        n_bills = np.int_(self.get_lognorm(self.BILLS_PP_MIN, self.BILLS_PP_AVG, self.NODES))

        total_bills = 0
        bill_ids = []
        for i in range(self.NODES):
            # In case we have more bills than cents.
            if n_bills[i] > totals[i]:
                n_bills[i] = totals[i]

            # Generate bill IDs buckets.
            node_bill_ids = []
            for j in range(n_bills[i]):
                node_bill_ids.append(total_bills)
                total_bills += 1
            bill_ids.append(node_bill_ids)

        # Set next bill ID counter to the next bill ID (last ID + 1).
        self.next_bill_id = bill_ids[-1][-1] + 1

        # Generate bills in parallel.
        with Pool(self.PROCESSES) as pool:
            # res: [node_id] => (bills_size_part, bills_cluster_part)
            res = pool.starmap(self.generate_node_bills, [(bill_ids[x], totals[x], x) for x in range(self.NODES)])

        # Save generated results into global vars.
        for i in range(self.NODES):
            bills_size_part, bills_cluster_part = res[i]
            # Save bills IDs into wallets.
            self.wallets[i] = list(bills_size_part.keys())
            self.bills_size.update(bills_size_part)
            self.bills_cluster.update(bills_cluster_part)

    @staticmethod
    def split_int(num, n_pieces) -> list:
        """Splits integer num into # of random n_pieces."""
        assert num >= n_pieces >= 1

        pieces = []
        for i in range(n_pieces - 1):
            pieces.append(random.randint(1, num - (n_pieces - i) + 1))
            num -= pieces[-1]
        pieces.append(num)

        return pieces

    @staticmethod
    def bit_distance(id1, id2):
        """Get mathematical distance between 2 integers."""
        return bin(id1 ^ id2).count("1")

    @classmethod
    def send_amount(
        cls,
        from_node_id: int,
        to_node_id: int,
        amount: int,
        wallets: dict,
        bills_size: dict,
        bills_cluster: dict,
        free_bill_ids: list
    ):
        """
        Send an amount from one node to another.
        :param from_node_id:
        :param to_node_id:
        :param amount:
        :param wallets: Gets updated by a reference.
        :param bills_size: Gets updated by a reference.
        :param bills_cluster: Gets updated by a reference.
        :param free_bill_ids: List of free_bill_ids that can be used to split the bills.
        :return: Number of bills that participated in this transaction.
        """
        # We assume that the balance is correct, so we do not need to check it.
        # if check_balance:
        #     if self.get_balance(from_node_id) < amount:
        #         return False

        to_cluster_id = cls.node_to_cluster(to_node_id)
        from_cluster_id = cls.node_to_cluster(from_node_id)
        wallets[from_node_id] = sorted(
            wallets[from_node_id],
            key=lambda x: (
                # Sort by distance to the receiver's cluster first, excluding bills in sender's cluster.
                float("inf") if bills_cluster[x] == from_cluster_id else Simulator.bit_distance(
                    bills_cluster[x],
                    to_cluster_id,
                ),
                bills_size[x],  # Sort by the smallest bill size second.
            ),
            reverse=True,   # So we can pop the bills from the end.
        )

        bills_used = 0  # Number of bills that participated in this transaction.
        amount_left_to_send = amount
        # Sending until we run out of the needed amount or bills.
        while amount_left_to_send and len(wallets[from_node_id]):
            # Take the bill that we are operating with.
            bill_id = wallets[from_node_id].pop()
            bills_used += 1

            # If the bill is not enough to cover the transaction, we send the whole bill.
            if bills_size[bill_id] <= amount_left_to_send:
                # Send the whole bill.
                wallets[to_node_id].append(bill_id)

                amount_left_to_send -= bills_size[bill_id]

            # If the bill is more than we need, we split it and send a part.
            else:
                new_bill_id = free_bill_ids.pop()

                # Split the bill into two.
                # Lower first bill's size and save it back.
                bills_size[bill_id] -= int(amount_left_to_send)
                wallets[from_node_id].append(bill_id)
                # Create a new bill with the same size.
                bills_size[new_bill_id] = amount_left_to_send
                bills_cluster[new_bill_id] = bills_cluster[bill_id]  # Same cluster as the one we are splitting from.
                # Save the new bill into the receiver's wallet.
                wallets[to_node_id].append(new_bill_id)

                amount_left_to_send = 0

        return bills_used

    @staticmethod
    def check_wallet(wallets, bills, owner_id, msg=""):
        """
        Checks if all the bills in the wallet belong to their owner.
        :param wallets:
        :param bills:
        :param owner_id: Node ID
        :param msg: Optional message before the error output.
        :return:
        """
        global log

        for bill_id in wallets[owner_id]:
            if bills[bill_id]['owner'] != owner_id:
                log.error(
                    "{} Bill #{} is in the wallet of Node ID #{}, but has the owner set to #{}. Bill: {}".format(
                        msg,
                        bill_id,
                        owner_id,
                        bills[bill_id]['owner'],
                        bills[bill_id],
                    )
                )

    @staticmethod
    def get_balance_static(node_id: int, wallets: dict, bills_size: dict) -> float:
        """Get balance of a specified node using passed data."""
        total = 0.0
        for bill_id in wallets[node_id]:
            total += bills_size[bill_id]
        return total

    def get_balance(self, node_id: int) -> float:
        """Get balance of a specified node."""
        return self.get_balance_static(node_id, self.wallets, self.bills_size)

    @classmethod
    def merge_nodes_bills(cls, node_id: int, wallets: dict, bills_size: dict, bills_cluster: dict) -> list:
        """
        Combines all node's bills that can be combined.
        :param node_id: Node ID for which we are trying to merge the bills.
        :param wallets: Gets updated by reference.
        :param bills_size: Gets updated by reference.
        :param bills_cluster: Gets updated by reference.
        :return: List of released Bill IDs.
        """

        # List of released Bill IDs.
        released_bill_ids = []

        # Sort all node's bills by cluster_id.
        wallets[node_id] = sorted(wallets[node_id], key=lambda x: bills_cluster[x])

        # Iterate over all bills and see if any could be combined.
        i = 1
        while i < len(wallets[node_id]):
            bill1_id = wallets[node_id][i-1]
            bill2_id = wallets[node_id][i]

            if bills_cluster[bill1_id] == bills_cluster[bill2_id]:
                # This will combine the bills and remove second id from the wallet, so no need to iterate i.

                if bill1_id == bill2_id:
                    raise Exception("Trying to merge the same bill with itself! #{} and #{}".format(bill1_id, bill2_id))
                wallets_s = len(wallets[node_id])
                bills_size_s = len(bills_size)
                bills_cluster_s = len(bills_cluster)
                released_bill_ids.append(
                    cls.combine_two_bills(bill1_id, bill2_id, node_id, wallets, bills_size, bills_cluster)
                )
                if wallets_s == len(wallets[node_id]):
                    raise Exception("Wallets did not change size after merging!")
                if bills_size_s == len(bills_size):
                    raise Exception("bills_size did not change size after merging!")
                if bills_cluster_s == len(bills_cluster):
                    raise Exception("bills_cluster did not change size after merging!")
            else:
                i += 1

        return released_bill_ids

    @staticmethod
    def combine_two_bills(bill1_id: int, bill2_id: int, node_id: int, wallets: dict, bills_size: dict, bills_cluster: dict) -> int:
        """
        Combines two bills into the first one. This should only be done for the same owner on the same cluster.
        :param bill1_id: Bill ID of the first bill to merge.
        :param bill2_id: Bill ID of the second bill to merge.
        :param node_id: Bills owner node_id.
        :param wallets: Gets updated by reference.
        :param bills_size: Gets updated by reference.
        :param bills_cluster: Gets updated by reference.
        :return: Released Bill ID. It needs to be saved into self.next_bill_ids
        """
        global log

        if bill1_id not in wallets[node_id] or bill2_id not in wallets[node_id]:
            raise ValueError(
                "Trying to combine two bills, but they do not belong to the same owner. "
                "Bill1 ID {} Bill2 ID {}".format(
                    bill1_id,
                    bill2_id,
                )
            )

        if bills_cluster[bill1_id] != bills_cluster[bill2_id]:
            raise ValueError(
                "Trying to combine two bills, but they do not belong to the same cluster. "
                "Bill1 ID {} belongs to cluster {} Bill2 ID {} belongs to cluster {}".format(
                    bill1_id,
                    bill2_id,
                    bills_cluster[bill1_id],
                    bills_cluster[bill2_id],
                )
            )

        # Add the value from 2 to 1 bill.
        bills_size[bill1_id] += bills_size[bill2_id]

        # Delete Bill 2 info.
        wallets[node_id].remove(bill2_id)
        del(bills_size[bill2_id])
        del(bills_cluster[bill2_id])

        # Return released bill ID.
        return bill2_id

    @classmethod
    def split_bill(cls, bill, amount_needed):
        """
        Splits a bill into two. One of the amount needed. Thread safe.
        You need to do the following with the returned values:
        1. Replace the old bill.
        2. Add new bill to self.bills
        3. Add the new bill to owner's wallet.
        :param bill: Old bill object.
        :param amount_needed: Float of the amount of the new bill (less than the old bill).
        :return: (new bill object, updated old bill)
        """

        assert amount_needed < bill['size']

        # Lower the size of previous bill.
        bill['size'] -= float(amount_needed)

        # Create a new bill.
        # {size: 123, owner: node_id, cluster: cluster_id}
        new_bill = {
            'size': amount_needed,
            'owner': bill['owner'],
            'cluster': bill['cluster'],
        }
        return (
            new_bill,
            bill,
        )


if __name__ == '__main__':
    simulator = Simulator(debug=False)
    simulator.output_system_status(into_log=False)
    simulator.run()
    simulator.output_system_status(into_log=False)

