'''The Hivemind'''

import queue
import time
import numpy as np

from rlbot.botmanager.agent_metadata import AgentMetadata
from rlbot.botmanager.bot_helper_process import BotHelperProcess
from rlbot.utils import rate_limiter
from rlbot.utils.logging_utils import get_logger
from rlbot.utils.structures.bot_input_struct import PlayerInput
from rlbot.utils.structures.game_data_struct import GameTickPacket, FieldInfoPacket
from rlbot.utils.structures.ball_prediction_struct import BallPrediction
from rlbot.utils.structures.game_interface import GameInterface
from rlbot.utils.game_state_util import Vector3, Rotator

PI = np.pi

class ExampleHivemind(BotHelperProcess):

    # Some terminology:
    # hivemind = the process which controls the drones.
    # drone = a bot under the hivemind's control.

    def __init__(self, agent_metadata_queue, quit_event, options):
        super().__init__(agent_metadata_queue, quit_event, options)

        # Sets up the logger. The string is the name of your hivemind.
        # Call this something unique so people can differentiate between hiveminds.
        self.logger = get_logger('Example Hivemind')

        # The game interface is how you get access to things
        # like ball prediction, the game tick packet, or rendering.
        self.game_interface = GameInterface(self.logger)

        # Running indices is a set of bot indices
        # which requested this hivemind with the same key.
        self.running_indices = set()


    def try_receive_agent_metadata(self):
        while True:  # will exit on queue.Empty
            try:
                # Adds drone indices to running_indices.
                single_agent_metadata: AgentMetadata = self.metadata_queue.get(timeout=0.1)
                self.running_indices.add(single_agent_metadata.index)
            except queue.Empty:
                return
            except Exception as ex:
                self.logger.error(ex)


    def start(self):
        """Runs once, sets up the hivemind and its agents."""
        # Prints an activation message into the console.
        # This let's you know that the process is up and running.
        self.logger.info("Hello World!")
        
        # Loads game interface.
        self.game_interface.load_interface()

        # Wait a moment for all agents to have a chance to start up and send metadata.
        self.logger.info("Snoozing for 3 seconds; give me a moment.")
        time.sleep(3)
        self.try_receive_agent_metadata()

        # This is how you access field info.
        # First create the initialise the object...
        field_info = FieldInfoPacket()
        # Then update it.
        self.game_interface.update_field_info_packet(field_info)

        # Same goes for the packet, but that is
        # also updated in the main loop every tick.
        packet = GameTickPacket()
        self.game_interface.update_live_data_packet(packet)
        # Ball prediction works the same. Check the main loop.

        # Create a Ball object for the ball that holds its information.        
        self.ball = Ball()

        # Create a Drone object for every drone that holds its information.
        self.drones = []
        for index in range(packet.num_cars):
            if index in self.running_indices:
                self.drones.append(Drone(index, packet.game_cars[index].team))

        # Other attribute initialisation.
        self.state = State.SETUP
        self.game_time = 0.0
        self.pinch_target = None
        self.pinch_time = 0.0
        
        # Runs the game loop where the hivemind will spend the rest of its time.
        self.game_loop()

            
    def game_loop(self):
        """The main game loop. This is where your hivemind code goes."""

        # Setting up rate limiter.
        rate_limit = rate_limiter.RateLimiter(120)

        packet = GameTickPacket()
        ball_prediction = BallPrediction()

        # Nicknames the renderer to shorten code.
        draw = self.game_interface.renderer

        # MAIN LOOP:
        while True:

            # Begins rendering at the start of the code because 
            # according to dtracers it is not bad practice and makes my life easier.
            # https://discordapp.com/channels/348658686962696195/446761380654219264/610879527089864737
            draw.begin_rendering()

            # PRE-PROCESSING:
            # Updating the game packet from the game.
            self.game_interface.update_live_data_packet(packet)

            # Updates the ball prediction.          
            self.game_interface.update_ball_prediction(ball_prediction)

            # Processing ball data.
            self.ball.pos = a3v(packet.game_ball.physics.location)
            self.ball.vel = a3v(packet.game_ball.physics.velocity)

            # Processing drone data.
            for drone in self.drones:
                drone.pos = a3v(packet.game_cars[drone.index].physics.location)
                drone.rot = a3r(packet.game_cars[drone.index].physics.rotation)
                drone.vel = a3v(packet.game_cars[drone.index].physics.velocity)
                drone.orient_m = orient_matrix(drone.rot)

                # Reset ctrl every tick.
                # PlayerInput is practically identical to SimpleControllerState.
                drone.ctrl = PlayerInput()

            # Game time.
            self.game_time = packet.game_info.seconds_elapsed

            # Example Team Pinches (2 bots only)
            # There's nothing stopping you from doing it with more ;) Give it a shot!
            if len(self.drones) == 2:

                # Sorts the drones left to right.
                right_to_left_drones = sorted(self.drones, key=lambda drone: drone.pos[0]*team_sign(drone.team))
                right = right_to_left_drones[0]
                left = right_to_left_drones[1]

                # Bots get boost and go to wait positions.
                if self.state == State.SETUP:
                    # Some guide positions.
                    right_boost = a3l([-3072.0, -4096.0, 71.1])*team_sign(right.team)
                    right_wait = a3l([-1792.0, -4184.0, 71.1])*team_sign(right.team)
                    left_boost = right_boost * a3l([-1,1,1])
                    left_wait = right_wait * a3l([-1,1,1])

                    # First get boost and then go to wait position.
                    if right.boost < 100:
                        right.ctrl = slow_to_pos(right, right_boost)
                    else:
                        right.ctrl = slow_to_pos(right, right_wait)

                    if left.boost < 100:
                        left.ctrl = slow_to_pos(left, left_boost)
                    else:
                        left.ctrl = slow_to_pos(left, left_wait)

                    # TODO slow_to_pos. Should slow down as it gets closer.

                    # If both bots are in wait position, switch to WAIT state.
                    if sum(np.linalg.norm(right.pos-right_boost), np.linalg.norm(left.pos-left_wait)) < 100:
                        self.state = State.WAIT

                # Bots try to face the ball, waiting for perfect moment to team pinch.
                elif self.state == State.WAIT:

                    # Filters out all the predictions where the ball is too far off the ground.
                    filtered_prediction = [[a3v(step.physics.location), step.game_seconds] for step in ball_prediction.slices if step.physics.location.z < 100]

                    self.game_interface.renderer.begin_rendering()
                    if len(filtered_prediction) > 0:
                        # Turns the predition into a numpy array for fast vectorized calculations.
                        filtered_prediction = np.array(filtered_prediction)
                        # Gets the vectors from the drones to the ball prediction.
                        right_to_prediction = filtered_prediction[:,0] - right.pos
                        left_to_prediction = filtered_prediction[:,0] - left.pos
                        # Calculates the distances.
                        right_distances = np.sqrt(np.einsum('ij,ij->i',right_to_prediction,right_to_prediction))
                        left_distances = np.sqrt(np.einsum('ij,ij->i',left_to_prediction,left_to_prediction))
                        # Filters out the predictions which are too close or too far.
                        good_distances = (1000 < right_distances < 3000) & (1000 < left_distances < 3000)
                        good_distance_targets = filtered_prediction[good_distances]

                        if len(good_distance_targets > 0):
                            # Pessimistic time error.
                            TIME_ERROR = 0.2
                            
                            # Getting the remaining distances after filter.
                            right_distances = right_distances[good_distances]
                            left_distances = left_distances[good_distances]

                            # Getting time estimates to go that distance. (Assuming boosting, and going in a straight line.)
                            right_times = right_distances**0.55 / 41.53
                            right_times[right_distances>2177.25] = 1/2300 * right_distances[right_distances>2177.25] + 0.70337
                            right_times += self.game_time - TIME_ERROR
                            
                            left_times = left_distances**0.55 / 41.53
                            left_times[left_distances>2177.25] = 1/2300 * left_distances[left_distances>2177.25] + 0.70337
                            left_times += self.game_time - TIME_ERROR

                            # Filters out the predictions which we can't get to.
                            good_times = (good_distance_targets[:1] > right_times) & (good_distance_targets[:1] > left_times)
                            valid_targets = good_distance_targets[good_times]

                            if len(valid_targets) > 0:
                                # TODO Pick a target.
                                # TODO set state to pinch.


                        # Each drone should try to face the ball.
                        for drone in self.drones:
                            drone.ctrl = face_pos(drone, ball.pos)

                elif self.state == State.PINCH:
                    # TODO Face target
                    # Wait until time to go. Use time estimate.
            
            else:
                draw.draw_string_2d(10, 10, 2, 2, 'This example version has only been coded for 2 HiveBots.')
                
                            
                

            '''
            # Sorts drones based on distance to ball.
            sorted_drones = sorted(self.drones, key=lambda drone: np.linalg.norm(drone.pos - self.ball.pos))

            if self.game_time > self.pinch_time:
                self.pinch_target = None

            if self.pinch_target is None:
                # Gets a rough estimate for which target locations are possible.
                second_closest_drone = sorted_drones[1]
                rough_estimate = np.linalg.norm(self.ball.pos - second_closest_drone.pos) / 1400

                # Filters out all that are sooner than our rough estimate.
                valid_targets = [step for step in self.ball_prediction.slices if step.game_seconds > self.game_time + rough_estimate]
                # Filters out all that are higher in the air.
                valid_targets = [step for step in valid_targets if step.physics.location.z < 100]
                
                if len(valid_targets) > 0:
                    self.pinch_target = a3v(valid_targets[0].physics.location)
                    self.pinch_time = valid_targets[0].game_seconds

            # Checks if the ball has been hit recently
            elif packet.game_ball.latest_touch.time_seconds + 0.2 > self.game_time:
                self.pinch_target = None

            else:
                # Get closest bots to attempt a team pinch.
                pinch_drones = sorted_drones[:2]
                self.team_pinch(pinch_drones)
            '''

            # Use this to send the drone inputs to the drones.
            for drone in self.drones:
                self.game_interface.update_player_input(drone.ctrl, drone.index)

            # Ending rendering before starting the example rendering.
            draw.end_rendering()

            # Some example rendering.
            self.draw_debug()



            # Rate limit sleep.
            rate_limit.acquire()


    def draw_debug(self):
        """Renders the ball prediction and drone indices."""
        self.game_interface.renderer.begin_rendering('debug')

        # Renders ball prediction
        path = [step.physics.location for step in self.ball_prediction.slices]
        self.game_interface.renderer.draw_polyline_3d(path, self.game_interface.renderer.pink())

        # Renders drone indices.
        for drone in self.drones:
            self.game_interface.renderer.draw_string_3d(drone.pos, 1, 1, str(drone.index), self.game_interface.renderer.white())

        # Team pinch info.
        if self.pinch_target is not None:
            self.game_interface.renderer.draw_rect_3d(self.pinch_target, 10, 10, True, self.game_interface.renderer.red())
            self.game_interface.renderer.draw_string_2d(10,10,2,2,str(self.pinch_time-self.game_time),self.game_interface.renderer.red())

        self.game_interface.renderer.end_rendering()


    def team_pinch(self, pinch_drones):
        '''
        # Finds time remaining to pinch.
        time_remaining = self.pinch_time - self.game_time

        # Sorts the pinch drones right to left 
        # so the right bot goes from the right and the left goes from the left.
        right_to_left_drones = sorted(pinch_drones, key=lambda drone: drone.pos[0]*team_sign(drone.team))

        for i, drone in enumerate(right_to_left_drones):
            # Finds vector towards goal from pinch target location.
            vector_to_goal = normalise(goal_pos*team_sign(drone.team)-self.pinch_target)
            # Finds 2D vector towards goal from pinch target.
            angle_to_goal = np.arctan2(vector_to_goal[1],vector_to_goal[0])
            # Angle offset for each bot participating in pinch.
            angle_offset = 2*PI / (len(pinch_drones) + 1)
            # Calculating approach vector.
            approach_angle = angle_to_goal + angle_offset * (i+1)
            approach_vector = np.array([np.cos(approach_angle), np.sin(approach_angle), 0])

            # Calculate target velocity
            distance_to_target = np.linalg.norm(self.pinch_target - drone.pos)
            target_velocity = distance_to_target / time_remaining
            # Offset target from the pinch target to drive towards.
            drive_target = self.pinch_target + (approach_vector * distance_to_target/2)
            # Calculates the pinch location in local coordinates.
            local_target = local(drone.orient_m, drone.pos, drive_target)
            # Finds 2D angle to target. Positive is clockwise.
            angle = np.arctan2(local_target[1], local_target[0])

            # Smooths out steering with modified sigmoid funcion.
            def special_sauce(x, a):
                """Modified sigmoid."""
                # Graph: https://www.geogebra.org/m/udfp2zcy
                return 2 / (1 + np.exp(a*x)) - 1

            # Calculates steer.
            drone.ctrl.steer = special_sauce(angle, -5)

            # Throttle controller.
            local_velocity = local(drone.orient_m, a3l([0,0,0]), drone.vel)
            # If I'm facing the wrong way, do a little drift.
            if abs(angle) > 2:
                drone.ctrl.throttle = 1.0
                drone.ctrl.handbrake = True
            else:
                drone.ctrl.throttle = 1 if local_velocity[0] < target_velocity else 0.0

            # Rendering of approach vectors.
            self.game_interface.renderer.begin_rendering(f'approach vectors {i}')
            self.game_interface.renderer.draw_line_3d(self.pinch_target, drive_target, self.game_interface.renderer.green())
            self.game_interface.renderer.end_rendering()
        '''
        '''
        error = 0.2

        for drone in pinch_drones:
            # Calculates the target location in local coordinates.
            local_target = local(drone.orient_m, drone.pos, self.pinch_target)
            # Finds 2D angle to target. Positive is clockwise.
            angle = np.arctan2(local_target[1], local_target[0])
            # Finds estimated time of arrival.
            ETA = self.game_time + local_target[0] / np.linalg.norm(drone.vel)

            # If pointing in right-ish direction, control throttle.
            if abs(angle) < 0.5:
                drone.ctrl.throttle = 1.0 if ETA > self.pinch_time + error else 0.0
            # If I'm facing the wrong way, do a little drift.
            elif abs(angle) > 1.6:
                drone.ctrl.throttle = 1.0
                drone.ctrl.handbrake = True
            # Just throttle if you're a bit wrong.
            else:
                drone.ctrl.throttle = 1.0

            # Smooths out steering with modified sigmoid funcion.
            def special_sauce(x, a):
                """Modified sigmoid."""
                # Graph: https://www.geogebra.org/m/udfp2zcy
                return 2 / (1 + np.exp(a*x)) - 1

            # Calculates steer.
            drone.ctrl.steer = special_sauce(angle, -5)

            # Dodge at the very end to pinch the ball.
            if 0.15 < self.pinch_time - self.game_time < 0.2:
                drone.ctrl.jump = True

            elif 0.0 < self.pinch_time - self.game_time  < 0.1:
                drone.ctrl.pitch = -1
                drone.ctrl.jump = True
        '''

def slow_to_pos(drone, position):
    pass

def turn_to_pos(drone, position):
    pass

                

# -----------------------------------------------------------

# UTILS:
# I copied over some of my HiveBot utils.
# Feel free to check out the full utilities file of HiveBot.

class Drone:
    """Houses the processed data from the packet for the drones.

    Attributes:
        index {int} -- The car's index in the packet.
        team {int} -- 0 if blue, else 1.
        pos {np.ndarray} -- Position vector.
        rot {np.ndarray} -- Rotation (pitch, yaw, roll).
        vel {np.ndarray} -- Velocity vector.
        orient_m {np.ndarray} -- Orientation matrix.
        ctrl {PlayerInput} -- The controls we want to send to the drone.
    """
    __slots__ = [
        'index',
        'team',
        'pos',
        'rot',
        'vel',
        'orient_m',
        'ctrl'  
    ]

    def __init__(self, index : int, team : int):
        self.index      : int           = index
        self.team       : int           = team
        self.pos        : np.ndarray    = np.zeros(3)
        self.rot        : np.ndarray    = np.zeros(3)
        self.vel        : np.ndarray    = np.zeros(3)
        self.orient_m   : np.ndarray    = np.identity(3)
        self.ctrl       : PlayerInput   = PlayerInput()


class Ball:
    """Houses the processed data from the packet for the ball.

    Attributes:
        pos {np.ndarray} -- Position vector.
        vel {np.ndarray} -- Velocity vector.
    """
    __slots__ = [
        'pos',
        'vel'
    ]

    def __init__(self):
        self.pos        : np.ndarray    = np.zeros(3)
        self.vel        : np.ndarray    = np.zeros(3)


# An example state enum.
# Since you are using a hivemind it's as if 
# all of your bots knew each other's state.
class State:
    SETUP = 0
    WAIT = 1
    PINCH = 2
    
# -----------------------------------------------------------

# FUNCTIONS FOR CONVERTION TO NUMPY ARRAYS:

def a3l(L : list) -> np.ndarray:
    """Converts list to numpy array.

    Arguments:
        L {list} -- The list to convert containing 3 elemets.

    Returns:
        np.array -- Numpy array with the same contents as the list.
    """
    return np.array([L[0], L[1], L[2]])

def a3r(R : Rotator) -> np.ndarray:
    """Converts rotator to numpy array.

    Arguments:
        R {Rotator} -- Rotator class containing pitch, yaw, and roll.

    Returns:
        np.ndarray -- Numpy array with the same contents as the rotator.
    """
    return np.array([R.pitch, R.yaw, R.roll])


def a3v(V : Vector3) -> np.ndarray:
    """Converts vector3 to numpy array.

    Arguments:
        V {Vector3} -- Vector3 class containing x, y, and z.

    Returns:
        np.ndarray -- Numpy array with the same contents as the vector3.
    """
    return np.array([V.x, V.y, V.z])

# -----------------------------------------------------------

# LINEAR ALGEBRA:

def normalise(V : np.ndarray) -> np.ndarray:
    """Normalises a vector.
    
    Arguments:
        V {np.ndarray} -- Vector.
    
    Returns:
        np.ndarray -- Normalised vector.
    """
    magnitude = np.linalg.norm(V)
    if magnitude != 0.0:
        return V / magnitude
    else:
        return V

def orient_matrix(R : np.ndarray) -> np.ndarray:
    """Converts from Euler angles to an orientation matrix.

    Arguments:
        R {np.ndarray} -- Pitch, yaw, and roll.

    Returns:
        np.ndarray -- Orientation matrix of shape (3, 3).
    """
    # Credits to chip https://samuelpmish.github.io/notes/RocketLeague/aerial_control/
    pitch : float = R[0]
    yaw   : float = R[1]
    roll  : float = R[2]

    CR : float = np.cos(roll)
    SR : float = np.sin(roll)
    CP : float = np.cos(pitch)
    SP : float = np.sin(pitch)
    CY : float = np.cos(yaw)
    SY : float = np.sin(yaw)

    A = np.zeros((3, 3))

    # front direction
    A[0,0] = CP * CY
    A[1,0] = CP * SY
    A[2,0] = SP

    # right direction (should be left but for some reason it is weird)
    A[0,1] = CY * SP * SR - CR * SY
    A[1,1] = SY * SP * SR + CR * CY
    A[2,1] = -CP * SR

    # up direction
    A[0,2] = -CR * CY * SP - SR * SY
    A[1,2] = -CR * SY * SP + SR * CY
    A[2,2] = CP * CR

    return A


def local(A : np.ndarray, p0 : np.ndarray, p1 : np.ndarray) -> np.ndarray:
    """Transforms world coordinates into local coordinates.
    
    Arguments:
        A {np.ndarray} -- The local orientation matrix.
        p0 {np.ndarray} -- World x, y, and z coordinates of the start point for the vector.
        p1 {np.ndarray} -- World x, y, and z coordinates of the end point for the vector.
    
    Returns:
        np.ndarray -- Local x, y, and z coordinates.
    """
    return np.dot(A.T, p1 - p0)


def team_sign(team : int) -> int:
    """Gives the sign for a calculation based on team.
    
    Arguments:
        team {int} -- 0 if Blue, 1 if Orange.
    
    Returns:
        int -- 1 if Blue, -1 if Orange
    """
    return 1 if team == 0 else -1

goal_pos = a3l([0,5300,0])
    