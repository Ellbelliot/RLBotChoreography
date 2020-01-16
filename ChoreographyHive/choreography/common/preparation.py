from typing import List

from rlbot.utils.game_state_util import CarState, Vector3, Rotator, Physics, GameState
from rlbot.utils.structures.game_data_struct import GameTickPacket
from rlbot.utils.structures.game_interface import GameInterface

from choreography.drone import Drone
from choreography.group_step import GroupStep, StepResult


class LetAllCarsSpawn(GroupStep):
    def __init__(self, game_interface: GameInterface, expected_num: float):
        self.game_interface = game_interface
        self.expected_num = expected_num
        self.start_time = None

    def perform(self, packet: GameTickPacket, drones: List[Drone]) -> StepResult:
        if not self.start_time:
            self.start_time = packet.game_info.seconds_elapsed

        elapsed = packet.game_info.seconds_elapsed - self.start_time

        start_x = -2000
        y_increment = 100
        start_y = -4000
        start_z = 40
        car_states = {}
        for drone in drones:
            car_states[drone.index] = CarState(
                Physics(location=Vector3(start_x, start_y + drone.index * y_increment, start_z),
                        velocity=Vector3(0, 0, 0),
                        rotation=Rotator(0, 0, 0)))
        self.game_interface.set_game_state(GameState(cars=car_states))
        active = packet.game_info.is_round_active

        return StepResult(finished=len(drones) >= self.expected_num and active or elapsed > 30)